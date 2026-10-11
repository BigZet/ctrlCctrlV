"""Cross-platform OCR candidate experiment; no screen or clipboard access.

Run in a separate venv with rapidocr==3.10.0 and onnxruntime==1.31.0.
Only synthetic fixture contents are written to the report.
"""
import argparse
from difflib import SequenceMatcher
import hashlib
from importlib.metadata import version
import io
import json
from pathlib import Path
import platform
import time

from scripts.ocr_layout import reconstruct_characters

MODEL_HASHES = {
    'ch_PP-OCRv5_det_mobile.onnx': '4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae',
    'eslav_PP-OCRv5_rec_mobile.onnx': '08705d6721849b1347d26187f15a5e362c431963a2a62bfff4feac578c489aab',
    'cyrillic_PP-OCRv5_rec_mobile.onnx': '90f761b4bfcce0c8c561c0cb5c887b0971d3ec01c32164bdf7374a35b0982711',
    'en_PP-OCRv5_rec_mobile.onnx': 'c3461add59bb4323ecba96a492ab75e06dda42467c9e3d0c18db5d1d21924be8',
}


def rectangle(text, box):
    points = [[float(x),float(y)] for x,y in box]
    left, right = min(p[0] for p in points),max(p[0] for p in points)
    top, bottom = min(p[1] for p in points),max(p[1] for p in points)
    return {'text':str(text),'x':left,'y':(top+bottom)/2,'width':right-left,'height':bottom-top}


def blocks_from_result(result):
    if result.boxes is None or result.txts is None:
        return []
    output=[]
    for index,(text,box) in enumerate(zip(result.txts,result.boxes)):
        block=rectangle(text,box)
        chars=result.word_results[index] if result.word_results and index<len(result.word_results) else None
        block['characters']=[rectangle(char[0],char[2]) for char in chars] if chars else [rectangle(text,box)]
        output.append(block)
    return output


def main():
    import onnxruntime
    from PIL import Image
    import requests
    from rapidocr import RapidOCR,EngineType,LangDet,LangRec,ModelType,OCRVersion
    onnxruntime.disable_telemetry_events()
    parser=argparse.ArgumentParser()
    parser.add_argument('--fixtures',type=Path,default=Path('artifacts/fixtures-expanded'))
    parser.add_argument('--output',type=Path,default=Path('artifacts/rapidocr.json'))
    parser.add_argument('--language',choices=('eslav','cyrillic','en'),default='eslav')
    parser.add_argument('--models',type=Path,default=Path('artifacts/rapidocr-models'))
    args=parser.parse_args()
    params={'Global.model_root_dir':str(args.models),'Global.use_cls':False,'Global.max_side_len':4096,
            'EngineConfig.onnxruntime.intra_op_num_threads':4,'EngineConfig.onnxruntime.inter_op_num_threads':1,
            'Det.engine_type':EngineType.ONNXRUNTIME,'Det.lang_type':LangDet.CH,
            'Det.model_type':ModelType.MOBILE,'Det.ocr_version':OCRVersion.PPOCRV5,
            'Rec.engine_type':EngineType.ONNXRUNTIME,'Rec.lang_type':LangRec(args.language),
            'Rec.model_type':ModelType.MOBILE,'Rec.ocr_version':OCRVersion.PPOCRV5}
    fixtures=json.loads((args.fixtures/'manifest.json').read_text())
    started=time.monotonic()
    engine=RapidOCR(params=params)
    engine(str(args.fixtures/fixtures[0]['image']),return_word_box=True,return_single_char_box=True)
    model_files_used = ['ch_PP-OCRv5_det_mobile.onnx', f'{args.language}_PP-OCRv5_rec_mobile.onnx']
    for filename in model_files_used:
        if hashlib.sha256((args.models / filename).read_bytes()).hexdigest() != MODEL_HASHES[filename]:
            raise RuntimeError('Model checksum mismatch')
    preparation=time.monotonic()-started
    # Models may download during preparation; recognition must be entirely local.
    def offline(*args,**kwargs):
        raise RuntimeError('Unexpected network request during OCR')
    requests.sessions.Session.request=offline
    rows=[]
    for fixture in fixtures:
        image=Image.open(args.fixtures/fixture['image']); image.load()
        expected=(args.fixtures/fixture['text']).read_text()
        started=time.monotonic()
        buffer=io.BytesIO(); image.save(buffer,format='PNG')
        result=engine(buffer.getvalue(),return_word_box=True,return_single_char_box=True)
        blocks=blocks_from_result(result)
        actual=reconstruct_characters(blocks)
        elapsed=time.monotonic()-started
        row={'id':fixture['id'],'complexity':fixture.get('complexity','short'),
             'elapsed':elapsed,'passed_4s':elapsed<=4,'width':image.width,'height':image.height,
             'exact_ignoring_final_newline':actual.rstrip('\n')==expected.rstrip('\n'),
             'similarity':SequenceMatcher(None,expected.rstrip('\n'),actual.rstrip('\n'),autojunk=False).ratio(),
             'expected':expected,'actual':actual,'blocks':blocks,
             'stages': [float(value) if value is not None else None for value in result.elapse_list]}
        rows.append(row)
        print(f'{row["id"]}: {elapsed:.3f}s exact={row["exact_ignoring_final_newline"]}',flush=True)
    models=[{'filename':path.name,'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in sorted(args.models.glob('*.onnx'))]
    report={'engine':'RapidOCR / ONNX Runtime CPU','packages':{'rapidocr':version('rapidocr'),'onnxruntime':version('onnxruntime')},
            'system':platform.system(),'system_version':platform.mac_ver()[0] if platform.system()=='Darwin' else platform.release(),
            'architecture':platform.machine(),'language':args.language,'models':models,'preparation_elapsed':preparation,
            'model_files_used': model_files_used,
            'timing_scope':'ready image PNG encoding + full OCR + box layout reconstruction; no clipboard',
            'recognition_network':'disabled; HTTP attempts raise an error',
            'config':{'threads':4,'inter_threads':1,'detector':'PP-OCRv5 mobile','recognizer':'PP-OCRv5 mobile',
                      'classification':False,'max_side_len':4096,'word_boxes':True,'single_char_boxes':True},
            'results':rows}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':
    main()
