"""One recognition supplies text and boxes; never re-read a tight text crop."""

import json
import subprocess
import sys


def test_owned_native_reader_preserves_recognition_and_exact_close():
    # Keep vendor imports confined to the worker, as production does. This
    # records a reader contract, not language/model accuracy; actual PDFs are
    # inspected through DocumentExtractor separately.
    script = """
import json
from pathlib import Path
from types import SimpleNamespace
from PIL import Image
from docling.datamodel.accelerator_options import AcceleratorOptions
from docling_core.types.doc.base import BoundingBox, CoordOrigin
from ghimera import document_ocr as m

events = []
class Iterator:
    def __init__(self): self.i = 0
    def BoundingBox(self, level): return (3, 6 + self.i * 30, 93, 24 + self.i * 30)
    def GetUTF8Text(self, level):
        return ('交通部門公布碼頭建設計畫', '政府は港湾整備計画を公表した')[self.i]
    def Confidence(self, level): return 80.0
    def Next(self, level):
        self.i += 1
        return self.i < 2
class Reader:
    def __init__(self, **kwargs): events.append(('init', kwargs['lang']))
    def SetImage(self, image): events.append(('image', image.size))
    def Recognize(self): events.append(('recognize',)); return True
    def GetIterator(self): return Iterator()
    def DetectOrientationScript(self): return {'orient_deg': 90, 'orient_conf': 0.1}
    def SetRectangle(self, *args): raise AssertionError('must not re-recognize a tight line')
    def End(self): events.append(('end',))
m.PyTessBaseAPI = Reader
m.get_languages = lambda path: (path, ['chi_tra', 'jpn', 'osd'])
options = m.PageRecognitionOptions(lang=['chi_tra', 'jpn'], path='/fixture/models',
    psm=6, scale=3, mode='full_page', detect_orientation=True,
    minimum_orientation_confidence=15)
model = m.PageRecognitionOcr(enabled=True, artifacts_path=Path('/fixture/models'),
    options=options, accelerator_options=AcceleratorOptions(device='cpu', num_threads=1))
region = BoundingBox(l=0, t=0, r=100, b=100, coord_origin=CoordOrigin.TOPLEFT)
model.get_ocr_rects = lambda page: [region]
image = Image.new('RGB', (300, 300), 'white')
backend = SimpleNamespace(is_valid=lambda: True, get_page_image=lambda **kwargs: image)
page = SimpleNamespace(_backend=backend)
cells = []
model.post_process_cells = lambda values, page, result: cells.extend(values)
assert list(model(SimpleNamespace(), [page])) == [page]
assert [cell.text for cell in cells] == ['交通部門公布碼頭建設計畫', '政府は港湾整備計画を公表した']
assert all(cell.orig == cell.text and cell.from_ocr for cell in cells)
assert min(cells[0].rect.r_x0, cells[0].rect.r_x1, cells[0].rect.r_x2, cells[0].rect.r_x3) == 1
assert min(cells[0].rect.r_y0, cells[0].rect.r_y1, cells[0].rect.r_y2, cells[0].rect.r_y3) == 2
assert max(cells[0].rect.r_y0, cells[0].rect.r_y1, cells[0].rect.r_y2, cells[0].rect.r_y3) == 8
assert all(cell.confidence == 0.8 for cell in cells)
assert events.count(('recognize',)) == 1
model.close(); model.close()
assert events.count(('end',)) == 2  # exactly once per native reader
print(json.dumps({'lines': len(cells), 'events': events}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["lines"] == 2
