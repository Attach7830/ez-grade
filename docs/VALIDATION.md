# Pilot against your own grading

The goal is to find where the application saves teacher time without hiding transcription or scoring mistakes. Software tests use labeled synthetic examples and demonstrate application behavior only. They do not establish classroom accuracy or justify skipping teacher review.

## A manageable first pilot

Choose one short assessment format and approximately 6–10 previously graded papers. Prefer an assessment with several objective questions and a few short responses so each stage is easy to inspect. Start with one file per student. Use pseudonymous identifiers such as `pilot-01`; keep any identifier mapping on your own equipment.

Before looking at machine results, set aside **different whole student submissions** for prompt/rubric tuning and evaluation. A practical first split is 2–3 tuning papers and the remaining papers held out for evaluation. This is a workflow trial, not a statistically precise estimate. If a question type has only one or two responses, report that small denominator rather than generalizing.

Transcribe the evaluation answers yourself from the paper and retain your original grades. Correct an old grade only if you discover a genuine marking error, and record the reason; do not change the reference simply because the model disagrees. Preserve missing accents, wrong endings, spelling mistakes, punctuation, and actual blank answers in this reference. If the original remains unreadable even to you, document that case and report it separately instead of inventing ground truth.

Tune prompts or rubric wording only with the tuning papers. Freeze the answer key, accepted alternatives, accent/spelling policies, partial credit, model tag/digest, context limit, and prompt version before running the evaluation papers. Keep this frozen configuration for the entire evaluation batch. If you revise it after seeing evaluation results, those papers become development material; use a fresh held-out set for the next evaluation.

## Record the workflow time

Record elapsed teacher time for:

- **Baseline:** ordinary manual grading for a comparable batch, including any grade entry you normally do.
- **Scanning:** preparing/scanning papers, naming/importing files, assigning answer regions, and fixing orientation or image quality.
- **Review:** correcting transcriptions, checking both flagged and unflagged answers, changing scores, approving answers, and exporting.

Use the app's time entries for scanning, review, and baseline. Include time spent recovering from failed extraction or requests. Do not count an unattended model wait as teacher labor, but record it separately if it prevents you from working. Do not compare a full manual-grading baseline with only a subset of app review time.

The evaluator adds all recorded time for that assessment. Keep tuning and evaluation in separate assessments when you need separate time totals; its grade metrics can split response IDs, but its timer entries are assessment-wide.

## Inspect every answer

1. Verify the approved key against the original assessment and your policy.
2. For every scan, check the selected answer region against the full page. Watch for omitted lines, wrong question numbers, crossed-out or overwritten answers, and printed text accidentally included in the crop.
3. Compare the original image with the **first** OCR transcription. Record the literal reading before making corrections.
4. Compare the first draft score with your teacher reference. Inspect the quoted evidence and each criterion; a plausible explanation can still support a wrong score.
5. Correct transcription or scoring, then approve the final teacher decision. Inspect unflagged answers just as carefully as flagged ones during the pilot.

OCR and grading errors are different. For example, if a student wrote `esta`, OCR changed it to `está`, and the scorer gave full credit, the transcript has already changed the evidence. A rubric scorer could be internally consistent with that repaired text and still produce the wrong grade for the paper. The original image and separate error measurements make this visible.

## Export and prepare the teacher reference

Download the assessment's **audit JSON** from the app. This is a local download to your browser, containing original suggestions and edits; keep it on your equipment. It can also be fetched through the authenticated `/api/assessments/{id}/audit.json` route.

Copy `examples/pilot_truth_template.csv` to a private working directory and fill its columns:

| Column | Meaning |
| --- | --- |
| `response_id` | Exact response ID from the audit; not a student's name |
| `split` | Exactly `tuning` or `eval` |
| `question_type` | Useful task category: `choice`, `vocabulary`, `conjugation`, `sentence_completion`, `translation`, or `explanation` |
| `transcription` | Your literal transcription; empty only for a true blank |
| `points` | Your reference score, within that question's point limit |
| `blank` | Exactly `true` or `false` |

Use categories that distinguish what the question tests. The app's implementation types (`exact`, `rubric`, `choice`) are coarser than the categories needed for this evaluation. Keep question type labels consistent within a pilot.

Example rows below are **synthetic format examples**, not real pilot results. Replace their IDs with IDs from your own audit:

```csv
response_id,split,question_type,transcription,points,blank
example-response-1,tuning,conjugation,esta,0.5,false
example-response-2,eval,translation,"Yo tengo un perro.",2,false
example-response-3,eval,vocabulary,,0,true
```

Quote CSV fields containing commas, quotation marks, or line breaks. Preserve Spanish accents when saving; use UTF-8 CSV. Grade-reference rows can cover a subset of the audit, but the report states how many audit responses have no reference. Do not omit hard cases to improve the reported score.

## Run the evaluator locally

The evaluator uses only Python's standard library, so it can run on either your server or laptop. No inference or network request occurs. On Linux, from the source directory:

```bash
mkdir -p pilot-private
cp examples/pilot_truth_template.csv pilot-private/teacher_truth.csv
```

Put `audit.json` into `pilot-private/`, fill the CSV, then run:

```bash
python3 scripts/evaluate_pilot.py \
  --audit pilot-private/audit.json \
  --truth pilot-private/teacher_truth.csv \
  --out pilot-private/pilot-results.json
```

For a later evaluation, compare against an earlier split manifest to reject reused tuning response/submission IDs:

```bash
python3 scripts/evaluate_pilot.py \
  --audit pilot-private/later-audit.json \
  --truth pilot-private/later-truth.csv \
  --tuning-manifest pilot-private/pilot-results.json \
  --out pilot-private/later-results.json
```

The script rejects duplicate response IDs, mixed tuning/evaluation rows from the same submission, nonexistent IDs, invalid scores, and references above a question's maximum. It never overwrites an existing output file. The split manifest stores hashes of IDs. It cannot recognize the same physical paper reimported under new IDs, so continue to maintain separate sets of source documents.

Synthetic assessments require the explicit `--allow-synthetic` flag and produce a prominent **SYNTHETIC SOFTWARE TEST — not classroom accuracy** label. Do not present those outputs as classroom results.

## What the report measures

| Measure | Interpretation |
| --- | --- |
| Character error rate (CER) | Levenshtein character edits divided by reference characters, across scan answers with an initial transcription |
| Word error rate (WER) | Levenshtein word edits divided by reference words; words are separated by whitespace |
| Transcription mismatch count | Scan answers whose first reading differs from the teacher's literal text |
| Missing/failed transcription count | Separate count, excluded from CER/WER denominators rather than silently treated as blank text |
| Incorrectly awarded points | Sum of positive differences between first draft points and teacher points |
| Incorrectly withheld points | Magnitude of negative point differences |
| Grade agreement given output | Matching scores divided by responses that actually received an initial score; missing outputs are separately visible |
| Unflagged errors | Incorrect readings or scores without an initial machine review reason; all still require teacher approval |
| Blank handling | True blanks with invented text and nonblank responses read as blank |
| Teacher time | Scanning plus review, with recorded manual baseline and the difference if supplied |

Every measure is broken down by the `question_type` labels you supply. The report records the assessment's model tag/digest, prompt/rubric version, context limit, and job provenance.

The script uses the earliest immutable `transcription_suggested`/`transcription_failed` and `grade_suggested`/`grade_failed` events, even if later retries succeed. It does not use your corrected transcription or final approved score as the original prediction. A first grade can occur after a teacher has corrected the reading; that measures the assisted workflow you actually used, not fully autonomous grading. To isolate model scoring from OCR, also run typed teacher transcriptions in a separate assessment with the same frozen rubric and model.

For CER, Unicode NFC composition is normalized so the same accented character represented in two equivalent encodings does not count as an error. Accents, spelling, case, endings, and spacing are otherwise retained. WER tokenization ignores whitespace differences by definition. Error rates can exceed 100% when there are many inserted characters/words. An all-blank reference set has no meaningful character/word denominator, so the rate is `null` and blank-error counts should be used instead.

## Decide what to change

Review mismatches by question type and by failure stage. If multiple choice is accurate but handwritten conjugations need frequent repair, keep deterministic multiple-choice grading and manually transcribe the difficult answers. If normalization grants undeserved credit, narrow that question's rule or list individual alternatives with specific point values. If explanations take longer to review than to grade manually, retain the image/transcription workflow and postpone model scoring for that type.

Before expanding to a full class, require that no processing failure becomes a final score, every result remains inspectable, and the complete scanning/review process saves enough time to be useful. Set your acceptable discrepancy levels before evaluating the held-out set. A small clean batch is a reason to continue the pilot; it is not evidence that future unflagged answers can be accepted automatically.
