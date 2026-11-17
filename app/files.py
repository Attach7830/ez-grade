"""Local rasterization only. Original uploads are retained byte for byte."""
import math
from pathlib import Path
from uuid import uuid4
import warnings
from PIL import Image, ImageOps
import pypdfium2 as pdfium

MAX_PAGES = 10
MAX_PIXELS = 25_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def rasterize(path: Path, output: Path) -> list[dict]:
    output.mkdir(parents=True, exist_ok=True)
    pages = []
    if path.suffix.lower() == '.pdf':
        doc = pdfium.PdfDocument(str(path))
        try:
            if not 1 <= len(doc) <= MAX_PAGES:
                raise ValueError(f'Use one student per upload, up to {MAX_PAGES} pages.')
            for i in range(len(doc)):
                page = doc[i]
                try:
                    w, h = page.get_size()
                    if w <= 0 or h <= 0 or w * h * 4 > MAX_PIXELS:
                        raise ValueError('PDF page is too large. Rescan at a normal page size.')
                    bitmap = page.render(scale=2)
                    try:
                        image = bitmap.to_pil().convert('RGB')
                        dest = output / f'page-{i}.png'
                        image.save(dest)
                        pages.append({'path':str(dest),'width':image.width,'height':image.height})
                        image.close()
                    finally:
                        bitmap.close()
                finally:
                    page.close()
        finally:
            doc.close()
    else:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(path) as source:
                if source.format not in ('PNG','JPEG','WEBP'):
                    raise ValueError('Supported image formats: PNG, JPEG and WebP.')
                if getattr(source, 'n_frames', 1) != 1:
                    raise ValueError('Use a single image or a PDF, not an animated image.')
                image = ImageOps.exif_transpose(source).convert('RGB')
                if image.width * image.height > MAX_PIXELS:
                    raise ValueError('Image is too large; use at most 25 megapixels.')
                dest = output / 'page-0.png'
                image.save(dest)
                pages.append({'path':str(dest),'width':image.width,'height':image.height})
                image.close()
    return pages


def make_crop(page: dict, region: dict, directory: Path) -> Path:
    with Image.open(page['path']) as image:
        x,y,w,h = (region[k] for k in ('x','y','width','height'))
        box = (math.floor(x*image.width), math.floor(y*image.height),
               min(image.width, math.ceil((x+w)*image.width)), min(image.height, math.ceil((y+h)*image.height)))
        if box[2]-box[0] < 4 or box[3]-box[1] < 4:
            raise ValueError('Select a larger answer region.')
        dest = directory / f'crop-{uuid4().hex}.png'
        image.crop(box).save(dest)
        return dest
