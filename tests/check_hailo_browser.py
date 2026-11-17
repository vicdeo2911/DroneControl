"""Browser QA of HEF selection; substitutes Pi I/O, never accesses aircraft."""
import io
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

from PIL import Image
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pi_vision.studio.training_studio import create_app


def main():
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory)
        model=root/'runs'/'example'/'compiled'/'plane-target-h8.hef'
        model.parent.mkdir(parents=True)
        model.write_bytes(b'browser-test-placeholder')
        (model.parent/'model-input.json').write_text(json.dumps(dict(input_size=640)))
        weights=root/'runs'/'example'/'weights'/'best.pt'
        weights.parent.mkdir()
        weights.write_bytes(b'not-loaded-by-this-test')
        app=create_app(root/'data',camera='http://127.0.0.1:1',runs=root/'runs')
        output=io.BytesIO()
        Image.new('RGB',(960,540),'#314047').save(output,format='JPEG')
        content=output.getvalue()
        app.extensions['dataset'].add(content,'browser','val')
        result=dict(frame_size=[960,540],model_input_shape=[640,640,3],tiles=1,
            detections=[dict(label='plane',confidence=.7,box=[100,100,200,150])],
            elapsed_ms=16,inference_ms=13,model_sha256=hashlib.sha256(model.read_bytes()).hexdigest(),
            inspection_job='a'*32,preparation='full')
        active_sha=[result['model_sha256']]
        polls=[0]
        def camera_json(camera,path,data=None):
            if path=='/api/status':
                polls[0]+=1
                return dict(ready=True,model='test.hef',model_sha256=active_sha[0],
                    model_input_shape=[640,640,3],frame_size=[960,540],detections=result['detections'],
                    preview_age_seconds=.1,preview_fps_limit=15,frames=polls[0],fps=35,capture_fps=50,
                    frame_age_seconds=.08,inference_ms=13,instance_id='one',confidence_threshold=.55,
                    configured_detection_mode='full')
            raise ValueError('Synthetic camera endpoint unavailable')
        server=make_server('127.0.0.1',0,app,threaded=True)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with patch('pi_vision.studio.training_studio.inspect_on_pi',return_value=result) as inspect, \
                 patch('pi_vision.studio.training_studio.activate_on_pi',return_value=dict(activated=True,model='test.hef',
                    model_input_shape=[640,640,3])) as activate, \
                 patch('pi_vision.studio.training_studio.camera_json',side_effect=camera_json), \
                 patch('pi_vision.studio.training_studio.fetch_camera',return_value=(content,'9')),sync_playwright() as p:
                browser=p.chromium.launch(channel='msedge',headless=True)
                page=browser.new_page(viewport=dict(width=1440,height=1100))
                errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.route('**/camera/hailo.mjpg?*',lambda route:route.fulfill(body=content,content_type='image/jpeg'))
                page.goto(f'http://127.0.0.1:{server.server_port}',wait_until='domcontentloaded')
                page.locator('.thumb').first.click()
                page.locator('#modelFormat').select_option('hailo')
                page.wait_for_function("document.querySelector('#weights').value.endsWith('.hef')")
                assert page.locator('#previewSize').is_disabled()
                assert page.locator('#activateHef').is_disabled()
                assert page.locator('#predictLive').inner_text()=='Start live Pi preview'
                assert 'live viewing does not' in page.locator('#modelBackendInfo').inner_text()
                page.locator('#predictImage').click()
                page.wait_for_function("document.querySelector('#predictionInfo').textContent.includes('Actual Hailo NPU')")
                assert inspect.call_count==1
                assert inspect.call_args.args[0]==model
                page.wait_for_function("!document.querySelector('#activateHef').disabled")
                page.once('dialog',lambda dialog:dialog.dismiss())
                page.locator('#activateHef').click()
                assert activate.call_count==0
                page.once('dialog',lambda dialog:dialog.accept())
                page.locator('#activateHef').click()
                page.wait_for_function("document.querySelector('#message').textContent.includes('Pi active detector:')")
                assert activate.call_count==1
                assert activate.call_args.args[1]['sha256']==result['model_sha256']
                page.locator('#predictPiFrame').click()
                page.wait_for_function("!document.querySelector('#predictPiFrame').disabled")
                assert inspect.call_count==2
                page.locator('#predictLive').click()
                page.wait_for_function("document.querySelector('#piLivePreview').naturalWidth === 960")
                assert page.locator('#predictLive').inner_text()=='Stop live Pi preview'
                assert page.locator('#confidence').is_hidden()
                page.wait_for_timeout(1200)
                assert polls[0]>=3
                assert inspect.call_count==2 and activate.call_count==1
                page.locator('#predictLive').click()
                assert page.locator('#piLivePreview').is_hidden()
                assert page.locator('#confidence').is_visible()
                page.locator('#predictLive').click()
                page.wait_for_function("document.querySelector('#piLivePreview').naturalWidth === 960")
                active_sha[0]='f'*64
                page.wait_for_function("document.querySelector('#message').textContent.includes('not active on the Pi')")
                assert page.locator('#piLivePreview').is_hidden()
                page.set_viewport_size(dict(width=390,height=844))
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
                Path('test-artifacts').mkdir(exist_ok=True)
                page.screenshot(path='test-artifacts/hailo-model-inspection.png',full_page=True)
                page.locator('#modelFormat').select_option('yolo')
                assert page.locator('#activateHef').is_hidden()
                assert page.locator('#previewSize').is_enabled()
                assert page.locator('#predictLive').inner_text()=='Start live preview'
                assert not errors,errors
                browser.close()
                print('HEF browser checks passed: test-gated activation, cancel/confirm, fixed input, image/camera tests, mobile layout, YOLO fallback.')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__=='__main__':main()
