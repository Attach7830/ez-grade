"""Synthetic layout workflow regressions; these are not handwriting accuracy tests.

The detector output is controlled here to exercise persistence, review gates,
stale edits, atomic cropping, and failures. test_regions.py separately runs real
local OCR on a labeled synthetic printed worksheet.
"""
from hashlib import sha256
from pathlib import Path
import sqlite3
import threading
import unittest
from unittest.mock import patch

import app.region_workflow as mapping
from PIL import Image
import test_workflow as workflow


def proposed(*ids):
    ids = ids or ('1', '2')
    return dict(detector_version=mapping.DETECTOR_VERSION, engine_version='SYNTHETIC OCR engine',
                proposals=[dict(question_id=q, page=0, x=0.2, y=0.1+i*0.3, width=0.5, height=0.15,
                                reason='SYNTHETIC detected answer rule.', flags=['Check the box on the original.'])
                           for i,q in enumerate(ids)], missing_question_ids=[], warnings=[])


class RegionRouteTests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp
    tearDown = workflow.WorkflowTests.tearDown
    checked = workflow.WorkflowTests.checked
    assessment = workflow.WorkflowTests.assessment
    typed = workflow.WorkflowTests.typed
    scan = workflow.WorkflowTests.scan
    crop = workflow.WorkflowTests.crop
    responses = workflow.WorkflowTests.responses
    response = workflow.WorkflowTests.response
    approve = workflow.WorkflowTests.approve

    def setup_scan(self, **assessment_options):
        assessment = self.assessment(**assessment_options)
        sub, content = self.scan(assessment['id'])
        return assessment, sub, content

    def detect(self, sub, output=None):
        with patch.object(mapping, 'detect_regions', return_value=proposed() if output is None else output):
            return self.checked(self.client.post(f"/api/submissions/{sub['id']}/region-detections"))

    def apply(self, detection, items=None, confirmed=True):
        if items is None:
            items = [{k:p[k] for k in ('question_id','revision','page','x','y','width','height')}
                     for p in detection['proposals']]
        return self.client.post(f"/api/region-detections/{detection['id']}/apply",
                                json=dict(confirmed=confirmed, regions=items))

    def source_directory(self, sub):
        with self.app.state.store.read() as con:
            row = con.execute('SELECT original_path FROM submissions WHERE id=?', (sub['id'],)).fetchone()
            return Path(row['original_path']).parent

    def test_detection_is_proposal_only_and_receives_no_key_or_scores(self):
        a, sub, content = self.setup_scan()
        before = self.responses(a['id'])
        self.assertIsNone(self.checked(self.client.get(f"/api/submissions/{sub['id']}/region-detections/latest")))
        with patch.object(mapping, 'detect_regions', return_value=proposed()) as detector:
            detection = self.checked(self.client.post(f"/api/submissions/{sub['id']}/region-detections"))
        self.assertEqual(self.responses(a['id']), before)
        self.assertEqual(detection['status'], 'ready')
        self.assertEqual(detection['source_sha256'], sha256(content).hexdigest())
        self.assertEqual(detection['key_version'], a['version'])
        self.assertEqual(set(detector.call_args.args[1][0]), {'id','type','prompt'})
        self.assertTrue(all(p['can_apply'] for p in detection['proposals']))
        self.assertTrue(all(r['points'] is None and r['transcription'] is None for r in before))
        self.assertEqual(self.checked(self.client.get(f"/api/region-detections/{detection['id']}")), detection)
        self.assertEqual(self.checked(self.client.get(f"/api/submissions/{sub['id']}/region-detections/latest")), detection)
        audit = self.checked(self.client.get(f"/api/assessments/{a['id']}/audit.json"))
        self.assertEqual(audit['region_detections'][0], detection)
        self.assertEqual(list(self.source_directory(sub).glob('crop-*.png')), [])

    def test_teacher_adjusted_subset_creates_crop_but_no_text_score_or_approval(self):
        a, sub, _ = self.setup_scan()
        detection = self.detect(sub)
        item = {k:detection['proposals'][0][k] for k in ('question_id','revision','page','x','y','width','height')}
        item['width'] = 0.6
        applied = self.checked(self.apply(detection, [item]))
        self.assertEqual(applied['applied'], 1)
        row = applied['responses'][0]
        self.assertEqual(row['region']['width'], 0.6)
        self.assertEqual(row['state'], 'pending')
        self.assertEqual(row['transcription_status'], 'pending')
        self.assertIsNone(row['transcription'])
        self.assertIsNone(row['points'])
        self.assertIsNone(row['suggestion'])
        self.assertFalse(row['transcription_confirmed'])
        self.assertEqual(self.client.get(row['crop_url']).status_code, 200)
        event = row['history'][-1]
        self.assertEqual(event['kind'], 'detected_region_applied')
        self.assertTrue(event['details']['teacher_adjusted'])
        self.assertEqual(event['details']['original_proposal']['width'], 0.5)
        self.assertEqual(event['details']['region']['width'], 0.6)
        remaining = next(r for r in self.responses(a['id']) if r['question_id'] == '2')
        self.assertIsNone(remaining['region'])
        self.assertEqual(remaining['revision'], 0)
        saved = self.checked(self.client.get(f"/api/region-detections/{detection['id']}"))
        self.assertEqual(saved['proposals'][0]['width'], 0.5)
        self.assertFalse(saved['proposals'][0]['can_apply'])
        self.assertTrue(saved['proposals'][1]['can_apply'])
        self.assertEqual(self.client.get(f"/api/assessments/{a['id']}/export.csv").status_code, 409)

    def test_missing_questions_remain_unresolved_and_can_be_manually_added(self):
        a, sub, _ = self.setup_scan()
        output = proposed('1')
        output['missing_question_ids'] = []  # Server derives this itself.
        detection = self.detect(sub, output)
        self.assertEqual(detection['missing_question_ids'], ['2'])
        question = next(q for q in detection['questions'] if q['question_id'] == '2')
        item = dict(question_id='2', revision=question['revision'], page=0, x=0.1, y=0.4, width=0.8, height=0.2)
        row = self.checked(self.apply(detection, [item]))['responses'][0]
        self.assertIsNone(row['history'][-1]['details']['original_proposal'])
        self.assertTrue(row['history'][-1]['details']['teacher_adjusted'])
        self.assertIsNone(next(r for r in self.responses(a['id']) if r['question_id']=='1')['points'])

    def test_ready_empty_detection_has_no_fabricated_blank_answers(self):
        a, sub, _ = self.setup_scan()
        output = proposed()
        output['proposals'] = []
        detection = self.detect(sub, output)
        self.assertEqual(detection['status'], 'ready')
        self.assertEqual(detection['missing_question_ids'], ['1','2'])
        self.assertTrue(all(r['transcription_status']=='pending' and r['points'] is None for r in self.responses(a['id'])))

    def test_confirmation_is_explicit_and_strict(self):
        a, sub, _ = self.setup_scan()
        detection = self.detect(sub)
        before = self.responses(a['id'])
        for confirmed in (False, 'true', 1):
            with self.subTest(confirmed=confirmed):
                self.assertEqual(self.apply(detection, confirmed=confirmed).status_code, 422)
        self.assertEqual(self.responses(a['id']), before)

    def test_ocr_failure_is_persisted_retryable_and_never_a_zero(self):
        a, sub, _ = self.setup_scan()
        before = self.responses(a['id'])
        with patch.object(mapping, 'detect_regions', side_effect=mapping.RegionDetectionError('SYNTHETIC local OCR timeout.')):
            detection = self.checked(self.client.post(f"/api/submissions/{sub['id']}/region-detections"))
        self.assertEqual(detection['status'], 'failed')
        self.assertIn('timeout', detection['error'])
        self.assertEqual(self.responses(a['id']), before)
        self.assertTrue(all(not q['can_apply'] for q in detection['questions']))
        item = dict(question_id='1', revision=0, page=0, x=0.1, y=0.1, width=0.5, height=0.2)
        self.assertEqual(self.apply(detection, [item]).status_code, 409)
        retry = self.detect(sub)
        self.assertEqual(retry['status'], 'ready')
        self.assertNotEqual(retry['id'], detection['id'])

    def test_invalid_detector_output_cannot_create_regions(self):
        a, sub, _ = self.setup_scan()
        before = self.responses(a['id'])
        variants = []
        for field,value in [('x',0.9), ('page',10), ('question_id','not-on-key'), ('width',True)]:
            output = proposed()
            output['proposals'][0][field] = value
            variants.append(output)
        output = proposed()
        output['proposals'][1]['question_id'] = '1'
        variants.append(output)
        for output in variants:
            with self.subTest(output=output):
                detection = self.detect(sub, output)
                self.assertEqual(detection['status'], 'failed')
                self.assertEqual(detection['proposals'], [])
                self.assertEqual(self.responses(a['id']), before)

    def test_existing_manual_regions_are_protected_and_bulk_apply_is_atomic(self):
        a, sub, _ = self.setup_scan()
        rows = self.responses(a['id'])
        saved = self.crop(rows[1])
        detection = self.detect(sub)
        self.assertFalse(detection['proposals'][1]['can_apply'])
        before = self.responses(a['id'])
        count = len(list(self.source_directory(sub).glob('crop-*.png')))
        self.assertEqual(self.apply(detection).status_code, 409)
        self.assertEqual(self.responses(a['id']), before)
        self.assertEqual(len(list(self.source_directory(sub).glob('crop-*.png'))), count)
        self.assertEqual(self.response(saved['id'])['crop_url'], saved['crop_url'])

    def test_saved_blank_transcription_and_zero_score_are_not_replaced(self):
        a, sub, _ = self.setup_scan()
        rows = self.responses(a['id'])
        self.checked(self.client.patch(f"/api/responses/{rows[0]['id']}", json={
            'revision':rows[0]['revision'], 'transcription':'', 'transcription_confirmed':True}))
        self.checked(self.client.patch(f"/api/responses/{rows[1]['id']}", json={
            'revision':rows[1]['revision'], 'transcription':'SYNTHETIC wrong answer', 'transcription_confirmed':True}))
        row = self.response(rows[1]['id'])
        self.checked(self.client.patch(f"/api/responses/{row['id']}", json={'revision':row['revision'], 'points':0}))
        self.approve(row['id'])
        before = self.responses(a['id'])
        detection = self.detect(sub)
        self.assertTrue(all(not p['can_apply'] for p in detection['proposals']))
        self.assertEqual(self.apply(detection).status_code, 409)
        self.assertEqual(self.responses(a['id']), before)

    def test_stale_response_rejects_entire_selection_and_cannot_be_bypassed(self):
        a, sub, _ = self.setup_scan()
        detection = self.detect(sub)
        row = self.responses(a['id'])[1]
        self.checked(self.client.patch(f"/api/responses/{row['id']}", json={
            'revision':row['revision'], 'transcription':'SYNTHETIC teacher correction', 'transcription_confirmed':True}))
        before = self.responses(a['id'])
        self.assertEqual(self.apply(detection).status_code, 409)
        items = [{k:p[k] for k in ('question_id','revision','page','x','y','width','height')} for p in detection['proposals']]
        items[1]['revision'] = self.response(row['id'])['revision']
        self.assertEqual(self.apply(detection, items).status_code, 409)
        self.assertEqual(self.responses(a['id']), before)
        self.assertEqual(list(self.source_directory(sub).glob('crop-*.png')), [])

    def test_duplicate_unknown_page_and_bounds_reject_without_mutation(self):
        a, sub, _ = self.setup_scan()
        detection = self.detect(sub)
        item = {k:detection['proposals'][0][k] for k in ('question_id','revision','page','x','y','width','height')}
        variants = [[item,item], [dict(item,question_id='different')], [dict(item,page=3)],
                    [dict(item,x=0.9)], [dict(item,width=-0.1)], [dict(item,page=True)]]
        before = self.responses(a['id'])
        for items in variants:
            with self.subTest(items=items):
                self.assertEqual(self.apply(detection, items).status_code, 422)
        self.assertEqual(self.responses(a['id']), before)

    def test_crop_failure_rolls_back_prior_crops_and_every_answer(self):
        a, sub, _ = self.setup_scan()
        detection = self.detect(sub)
        before = self.responses(a['id'])
        actual_crop = mapping.make_crop
        count = 0

        def fail_second(*args):
            nonlocal count
            count += 1
            if count == 2:
                raise ValueError('SYNTHETIC crop failure.')
            return actual_crop(*args)

        with patch.object(mapping, 'make_crop', side_effect=fail_second):
            self.assertEqual(self.apply(detection).status_code, 422)
        self.assertEqual(self.responses(a['id']), before)
        self.assertEqual(list(self.source_directory(sub).glob('crop-*.png')), [])

    def test_changed_source_page_rejects_applied_coordinates(self):
        a, sub, _ = self.setup_scan()
        detection = self.detect(sub)
        before = self.responses(a['id'])
        page = self.source_directory(sub) / 'page-0.png'
        page.write_bytes(page.read_bytes()+b'SYNTHETIC external modification')
        self.assertEqual(self.apply(detection).status_code, 409)
        self.assertEqual(self.responses(a['id']), before)

    def test_partial_image_write_is_cleaned_and_never_applied(self):
        a, sub, _ = self.setup_scan()
        detection = self.detect(sub)
        before = self.responses(a['id'])

        def fail_save(image, path, *args, **kwargs):
            Path(path).write_bytes(b'SYNTHETIC incomplete PNG')
            raise OSError('SYNTHETIC disk write error')

        with patch.object(Image.Image, 'save', fail_save):
            self.assertEqual(self.apply(detection).status_code, 422)
        self.assertEqual(self.responses(a['id']), before)
        self.assertEqual(list(self.source_directory(sub).glob('crop-*.png')), [])

    def test_repeated_apply_cannot_overwrite_a_previously_applied_region(self):
        a, sub, _ = self.setup_scan()
        detection = self.detect(sub)
        self.checked(self.apply(detection))
        before = self.responses(a['id'])
        self.assertEqual(self.apply(detection).status_code, 409)
        self.assertEqual(self.responses(a['id']), before)

    def test_detector_lock_allows_only_one_active_process_and_releases(self):
        _, sub, _ = self.setup_scan()
        entered, release = threading.Event(), threading.Event()
        outputs = []

        def slow_detector(*_):
            entered.set()
            if not release.wait(4):
                raise RuntimeError('Synthetic test timed out.')
            return proposed()

        def request():
            outputs.append(self.client.post(f"/api/submissions/{sub['id']}/region-detections"))

        with patch.object(mapping, 'detect_regions', side_effect=slow_detector):
            thread = threading.Thread(target=request)
            thread.start()
            try:
                self.assertTrue(entered.wait(3))
                self.assertEqual(self.client.post(f"/api/submissions/{sub['id']}/region-detections").status_code, 409)
            finally:
                release.set()
                thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outputs[0].status_code, 200)
        self.assertEqual(self.detect(sub)['status'], 'ready')

    def test_auth_and_approved_scan_requirements_apply_to_detection(self):
        a = self.assessment(approve=False)
        sub, _ = self.scan(a['id'])
        self.assertEqual(self.client.post(f"/api/submissions/{sub['id']}/region-detections").status_code, 409)
        typed = self.typed(a['id'], {'1':'B','2':'está'})
        self.assertEqual(self.client.post(f"/api/submissions/{typed['id']}/region-detections").status_code, 422)
        self.checked(self.client.post(f"/api/assessments/{a['id']}/approve"))
        detection = self.detect(sub)
        self.checked(self.client.post('/api/logout'))
        self.assertEqual(self.client.post(f"/api/submissions/{sub['id']}/region-detections").status_code, 401)
        self.assertEqual(self.client.get(f"/api/submissions/{sub['id']}/region-detections/latest").status_code, 401)
        self.assertEqual(self.client.get(f"/api/region-detections/{detection['id']}").status_code, 401)
        self.assertEqual(self.apply(detection).status_code, 401)

    def test_detection_persists_across_restart_and_schema2_migrates_unchanged(self):
        a, sub, _ = self.setup_scan()
        before = self.responses(a['id'])
        self.client.__exit__(None,None,None)
        with sqlite3.connect(self.settings.data_dir / 'grader.sqlite3') as con:
            con.execute('DROP TABLE region_detections')
            con.execute('PRAGMA user_version=2')
        self.restart()
        self.assertEqual(self.responses(a['id']), before)
        self.assertEqual(self.checked(self.client.get(f"/api/assessments/{a['id']}"))['prompt_hash'], a['prompt_hash'])
        detection = self.detect(sub)
        self.client.__exit__(None,None,None)
        self.restart()
        self.assertEqual(self.checked(self.client.get(f"/api/region-detections/{detection['id']}")), detection)
        self.checked(self.apply(detection))
        with sqlite3.connect(self.settings.data_dir / 'grader.sqlite3') as con:
            self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0], 3)

    def restart(self):
        self.app = workflow.create_app(self.settings, client=self.fake)
        self.client = workflow.TestClient(self.app, headers={'X-Grader-Request':'1'})
        self.client.__enter__()
        self.checked(self.client.post('/api/login', json={'password':workflow.PASSWORD}))


if __name__ == '__main__':
    unittest.main()
