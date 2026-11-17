"""Browser QA using temporary synthetic images; never contacts aircraft hardware."""
from pathlib import Path
import sys
import tempfile
import threading

from PIL import Image
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pi_vision.studio.training_studio import create_app
from test_training import BOXES


def main():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        app = create_app(root / 'data', camera='http://127.0.0.1:1', runs=root / 'runs')
        first, second = root / 'a.png', root / 'b.png'
        Image.new('RGB', (960, 540), '#314047').save(first)
        Image.new('RGB', (960, 540), '#456052').save(second)
        server = make_server('127.0.0.1', 0, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(channel='msedge', headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1100})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}')
                assert page.locator('#preparation').input_value() == 'full'
                assert page.locator('#imgsz').input_value() == '640'
                assert page.locator('#imgsz').is_enabled()
                assert page.locator('label[for="recordSeconds"]').count() == 1
                assert 'Up to 10,000 frames' in page.locator('#recordingPanel').inner_text()
                assert 'full frames to match current flight inference' in page.locator('body').inner_text()
                assert page.locator('#target option[value="yolo"]').inner_text() == 'Standard YOLO - best.pt'
                page.locator('#preparation').select_option('adaptive')
                assert page.locator('#imgsz').is_disabled()
                page.locator('#model').select_option('yolov8m.pt')
                page.locator('#model').select_option('yolov8s.pt')
                page.locator('#preparation').select_option('full')
                assert page.locator('#imgsz').is_enabled()
                page.locator('#preparation').select_option('adaptive')
                assert page.locator('#previewSize').is_enabled()
                page.locator('#previewPreparation').select_option('adaptive')
                assert page.locator('#previewSize').is_disabled()
                page.locator('#photos').set_input_files(str(first))
                page.locator('#import').click()
                page.wait_for_function("document.querySelectorAll('.thumb').length === 1")
                page.locator('#annotation').wait_for(state='visible')
                rect = page.locator('#annotation').bounding_box()
                page.mouse.move(rect['x'] + rect['width'] * .1, rect['y'] + rect['height'] * .2)
                page.mouse.down()
                page.mouse.move(rect['x'] + rect['width'] * .3, rect['y'] + rect['height'] * .4)
                page.mouse.up()
                page.locator('#targetClass').click()
                page.mouse.move(rect['x'] + rect['width'] * .6, rect['y'] + rect['height'] * .3)
                page.mouse.down()
                page.mouse.move(rect['x'] + rect['width'] * .8, rect['y'] + rect['height'] * .6)
                page.mouse.up()
                assert page.locator('#boxes button').count() == 2
                assert page.locator('#negative').is_disabled()
                page.locator('#save').click()
                page.wait_for_function("document.getElementById('counts').textContent.startsWith('1 / 1')")
                row = app.extensions['dataset'].list()[0]
                assert row['reviewed'] and [b['class_id'] for b in row['boxes']] == [0, 1]
                assert abs(row['boxes'][0]['w'] - .2) < .01
                # Independent validation recording, then portable dataset download.
                page.locator('#session').fill('flight-02')
                page.locator('#split').select_option('val')
                page.locator('#photos').set_input_files(str(second))
                page.locator('#import').click()
                page.wait_for_function("document.querySelectorAll('.thumb').length === 2")
                validation = app.extensions['dataset'].list()[1]
                app.extensions['dataset'].save(validation['id'], BOXES)
                page.locator('#refresh').click()
                page.wait_for_function("document.getElementById('counts').textContent.startsWith('2 / 2')")
                page.locator('#export').click()
                page.get_by_role('link', name='Download dataset ZIP').wait_for()
                import json
                snapshot = next((root/'data'/'exports').glob('*/preparation.json'))
                assert json.loads(snapshot.read_text())['profile'] == 'adaptive'
                with page.expect_download() as transfer:
                    page.get_by_role('link', name='Download dataset ZIP').click()
                assert Path(transfer.value.path()).stat().st_size > 0
                # Unavailable camera is a visible, recoverable error.
                page.locator('#capture').click()
                page.wait_for_function("document.getElementById('message').textContent.includes('Cannot fetch clean camera')")
                # Small layouts remain usable with no horizontal page overflow.
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                page.on('dialog', lambda dialog: dialog.accept())
                page.locator('#discardImage').click()
                page.wait_for_function("document.querySelectorAll('.thumb').length === 1")
                # Batch selection and Delete key operate on library images.
                dataset = app.extensions['dataset']
                for color in ('red', 'blue', 'yellow'):
                    import io
                    content = io.BytesIO()
                    Image.new('RGB', (160, 96), color).save(content, format='PNG')
                    dataset.add(content.getvalue(), 'batch-test', 'train')
                page.locator('#refresh').click()
                page.wait_for_function("document.querySelectorAll('.thumb').length === 4")
                page.locator('.thumb').nth(0).click()
                page.locator('.thumb').nth(2).click(modifiers=['Shift'])
                assert page.locator('#selectionCount').inner_text() == '3 selected'
                page.locator('.thumb').nth(1).click(modifiers=['Control'])
                assert page.locator('#selectionCount').inner_text() == '2 selected'
                page.locator('#library').press('Delete')
                page.wait_for_function("document.querySelectorAll('.thumb').length === 2")
                assert len(dataset.list()) == 2
                assert not errors, errors
                browser.close()
                print('Browser checks passed: import, two-class drawing, saved labels, validation split, ZIP, camera errors, mobile layout.')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    main()
