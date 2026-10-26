"""09—12共用的缓存、表格、坐标和离线展示工具；无源图纸写入操作。"""
from pathlib import Path
from collections import defaultdict
import hashlib
import json
import csv
import sys
import math

PROJECT=Path(__file__).resolve().parent
sys.path.append(str(PROJECT.parent/'.cad_deps'))
import numpy as np
import pymupdf


def stage(n):
    paths=list(PROJECT.glob(f'{n:02d}_*'))
    if len(paths)!=1:raise ValueError(f'阶段{n}目录不唯一')
    return paths[0]


def pdf_path():
    paths=list(PROJECT.glob('*.pdf'))
    if len(paths)!=1:raise ValueError('Fsgy-surf根目录须有唯一参考PDF')
    return paths[0]


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def read_json(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def _default(obj):
    if isinstance(obj,np.ndarray):return obj.tolist()
    if isinstance(obj,np.generic):return obj.item()
    if isinstance(obj,Path):return str(obj)
    raise TypeError(type(obj).__name__)


def save_json(path,data):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    Path(path).write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False,default=_default),encoding='utf-8')


def write_csv(path,rows):
    if not rows:return
    keys=list(dict.fromkeys(k for row in rows for k in row))
    with Path(path).open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader()
        w.writerows({k:json.dumps(v,ensure_ascii=False,default=_default) if isinstance(v,(dict,list,tuple)) else v for k,v in r.items()} for r in rows)


def page_lines(page):
    words=[]
    for w in page.get_text('words'):
        rect=pymupdf.Rect(w[:4])*page.rotation_matrix
        words.append((rect.x0,(rect.y0+rect.y1)/2,w[4]))
    groups=[]
    for word in sorted(words,key=lambda w:w[1]):
        if not groups or abs(word[1]-groups[-1][0])>1.:
            groups.append([word[1],[word]])
        else:groups[-1][1].append(word)
    return [' '.join(w[2] for w in sorted(group,key=lambda w:w[0])) for y,group in groups]


# 总布置名义分舱与手册强度站位共同确认的肋距变换；不将肋号直接当米。
FRAME=np.array([-5.,0.,9.,11.,25.,34.,52.,69.,87.,105.,123.,141.,159.,177.,192.,208.,214.,220.,224.])
FRAME_X=np.array([-3.3,0.,5.94,7.4,18.6,25.8,40.2,53.8,68.2,82.6,97.,111.4,125.8,140.2,152.2,165.,169.2,173.4,176.2])


def frame_to_x(fr):
    if np.any(np.asarray(fr)<FRAME[0]) or np.any(np.asarray(fr)>FRAME[-1]):raise ValueError('肋号超出坐标表范围')
    return np.interp(fr,FRAME,FRAME_X)


def curve_summary(rows,key='difference'):
    values=[float(r[key]) for r in rows]
    if not values:return dict(count=0,mean_abs=None,max_abs=None)
    return dict(count=len(values),mean_abs=float(np.mean(np.abs(values))),max_abs=float(np.max(np.abs(values))))


def write_dashboard(path,title,intro,cases,notes,stats=None,links=None):
    template=(PROJECT/'pipeline_dashboard.html').read_text(encoding='utf-8')
    payload=dict(title=title,intro=intro,cases=cases,notes=notes,stats=stats or {},links=links or {})
    text=json.dumps(payload,ensure_ascii=False,allow_nan=False,default=_default).replace('</','<\\/')
    Path(path).write_text(template.replace('__DATA__',text),encoding='utf-8')
