"""How well can a local model re-derive Active Guard's people attributes from a best-shot image?

The answer key is Active Guard's own label per attribute group, kept only where Active Guard was confident
(>= 0.5, the same cut the importer uses). It is another model's opinion, not human truth: agreement is what
matters here, because search compares the uploaded photo's attributes against exactly these stored labels.

    python evaluate.py --models clip,pulc,qwen --limit 1000 --qwen-limit 100
"""
import argparse
import colorsys
import json
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).parent
SAMPLE, RESULTS, MODELS = HERE / 'sample', HERE / 'results', HERE / 'models'
COLOR_GROUPS = ('hair_color', 'upper_color', 'lower_color', 'bag_color', 'shoes_color')
# Neighbouring colour names two models can reasonably disagree on; reported separately from strict matches.
FAMILIES = [{'black', 'gray'}, {'white', 'gray', 'beige'}, {'blue', 'purple', 'violet'}, {'brown', 'orange', 'beige'},
            {'red', 'pink', 'orange'}, {'yellow', 'gold', 'golden', 'orange'}]


def load(limit):
    rows = [json.loads(line) for line in (SAMPLE / 'labels.jsonl').open(encoding='utf-8')][:limit]
    labels = defaultdict(set)
    for r in rows:
        for group, scores in r['scores'].items():
            table = scores[0] if isinstance(scores, list) and scores else scores
            if isinstance(table, dict):
                labels[group].update(table)
                label, score = max(table.items(), key=lambda kv: float(kv[1]))
                r.setdefault('truth', {})[group] = label if float(score) >= 0.5 else None
    return rows, {g: sorted(v) for g, v in labels.items()}


# ---------------- CLIP: zero-shot, one prompt per label ----------------
def phrase(group, label):
    c = {'gold': 'golden', 'violet': 'purple'}.get(label, label)
    fixed = {
        ('gender', 'male'): 'a man', ('gender', 'female'): 'a woman',
        ('age', '0-10'): 'a young child', ('age', '11-20'): 'a teenager', ('age', '21-60'): 'an adult', ('age', '61+'): 'an elderly person',
        ('hair_style', 'long-hair'): 'a person with long hair', ('hair_style', 'short-hair'): 'a person with short hair',
        ('hair_style', 'hat'): 'a person wearing a hat',
        ('upper_garment', 'long-sleeves'): 'a person wearing a long-sleeved top', ('upper_garment', 'short-sleeves'): 'a person wearing a short-sleeved top',
        ('lower_garment', 'long'): 'a person wearing long trousers', ('lower_garment', 'short'): 'a person wearing shorts',
    }
    if (group, label) in fixed:
        return fixed[group, label]
    yes_no = {'sunglasses': 'sunglasses', 'face_mask': 'a face mask', 'beard': 'a beard', 'bag': 'a bag'}
    if group in yes_no:
        verb = 'carrying' if group == 'bag' else ('with' if group == 'beard' else 'wearing')
        return f'a person {verb} {yes_no[group]}' if label == 'yes' else f'a person without {yes_no[group]}'
    noun = {'hair_color': 'hair', 'upper_color': 'shirt', 'lower_color': 'trousers', 'bag_color': 'bag', 'shoes_color': 'shoes'}.get(group)
    return f'a person with {c} {noun}' if group == 'hair_color' else f'a person wearing {c} {noun}' if noun else f'a person, {label}'


class Clip:
    def __init__(self, labels, arch='ViT-B-32', pretrained='openai'):
        import open_clip
        import torch
        self.torch = torch
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(arch, pretrained=pretrained)
        self.model.eval()
        tokenizer = open_clip.get_tokenizer(arch)
        self.name = f'clip-{arch}'
        self.text = {}
        with torch.no_grad():
            for group, options in labels.items():
                prompts = [f'a surveillance photo of {phrase(group, o)}' for o in options]
                t = self.model.encode_text(tokenizer(prompts))
                self.text[group] = (options, t / t.norm(dim=-1, keepdim=True))

    def predict(self, image):
        with self.torch.no_grad():
            v = self.model.encode_image(self.preprocess(image).unsqueeze(0))
            v = v / v.norm(dim=-1, keepdim=True)
            out = {}
            for group, (options, t) in self.text.items():
                out[group] = options[int((v @ t.T).argmax())]
            return out


# ---------------- PULC (PaddleClas, PA100K 26 attributes) + colour sampling ----------------
def color_name(rgb, options):
    r, g, b = (x / 255 for x in rgb)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    h *= 360
    if v < 0.22:
        name = 'black'
    elif s < 0.18:
        name = 'white' if v > 0.8 else 'gray'
    elif (h < 45 or h >= 345) and v < 0.65:
        name = 'brown'
    elif 20 <= h < 65 and s < 0.35 and v > 0.6:
        name = 'beige'
    else:
        name = next(n for limit, n in ((15, 'red'), (40, 'orange'), (70, 'yellow'), (170, 'green'), (255, 'blue'),
                                       (290, 'purple'), (345, 'pink'), (361, 'red')) if h < limit)
    for candidate in (name, {'yellow': 'gold', 'purple': 'violet', 'beige': 'white'}.get(name), {'yellow': 'golden'}.get(name)):
        if candidate in options:
            return candidate
    return name


def region_color(image, box, options):
    w, h = image.size
    x0, y0, x1, y1 = box
    crop = np.asarray(image.crop((int(x0 * w), int(y0 * h), max(int(x1 * w), int(x0 * w) + 1), max(int(y1 * h), int(y0 * h) + 1))).convert('RGB'))
    return color_name(np.median(crop.reshape(-1, 3), axis=0), options)


class Pulc:
    name = 'pulc+color'

    def __init__(self, labels):
        from paddle import inference
        folder = MODELS / 'person_attribute_infer'
        if not folder.exists():
            import tarfile, urllib.request, io
            MODELS.mkdir(exist_ok=True)
            data = urllib.request.urlopen('https://paddleclas.bj.bcebos.com/models/PULC/person_attribute_infer.tar').read()
            tarfile.open(fileobj=io.BytesIO(data)).extractall(MODELS)
        model = next(folder.glob('*.pdmodel'), None) or next(folder.glob('*.json'))
        config = inference.Config(str(model), str(folder / 'inference.pdiparams'))
        config.disable_gpu()
        config.set_cpu_math_library_num_threads(8)
        # The published model was exported by an old Paddle; Paddle 3.x's oneDNN fusion passes break on it
        # ("OneDnnContext does not have the input Filter"), so run it unfused.
        config.switch_ir_optim(False)
        config.disable_mkldnn()
        self.predictor = inference.create_predictor(config)
        self.labels = labels

    def predict(self, image):
        x = np.asarray(image.convert('RGB').resize((192, 256)), dtype=np.float32) / 255.0
        x = ((x - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]).transpose(2, 0, 1)[None].astype(np.float32)
        inp = self.predictor.get_input_handle(self.predictor.get_input_names()[0])
        inp.copy_from_cpu(x)
        self.predictor.run()
        p = self.predictor.get_output_handle(self.predictor.get_output_names()[0]).copy_to_cpu()[0]
        if p.min() < 0 or p.max() > 1:
            p = 1 / (1 + np.exp(-p))
        age = ['under18', '21-60', '61+'][int(np.argmax(p[19:22]))]
        out = {
            'gender': 'female' if p[22] > 0.5 else 'male',
            'age': frozenset({'0-10', '11-20'}) if age == 'under18' else age,       # PA100K has one class below 18
            'hair_style': 'hat' if p[0] > 0.5 else frozenset({'long-hair', 'short-hair'}),  # PA100K has no hair length
            'upper_garment': 'long-sleeves' if p[3] > p[2] else 'short-sleeves',
            'lower_garment': 'short' if p[12] > p[11] else 'long',
            'sunglasses': 'yes' if p[1] > 0.3 else 'none',                           # PA100K "Glasses", not sunglasses
            'bag': 'yes' if max(p[15:18]) > 0.5 else 'none',
        }
        opts = lambda g: self.labels.get(g, [])
        out['hair_color'] = region_color(image, (0.35, 0.0, 0.65, 0.08), opts('hair_color'))
        out['upper_color'] = region_color(image, (0.3, 0.22, 0.7, 0.48), opts('upper_color'))
        out['lower_color'] = region_color(image, (0.3, 0.58, 0.7, 0.85), opts('lower_color'))
        out['shoes_color'] = region_color(image, (0.3, 0.93, 0.7, 1.0), opts('shoes_color'))
        return {g: v for g, v in out.items() if g in self.labels}


# ---------------- Qwen2.5-VL through the local Ollama, forced into the stored vocabulary ----------------
class Qwen:
    def __init__(self, labels, model='qwen2.5vl:3b', url='http://127.0.0.1:11434'):
        import httpx
        self.http, self.model, self.labels = httpx.Client(base_url=url, timeout=600), model, labels
        self.name = model

    def predict(self, image):
        import base64, io
        buf = io.BytesIO()
        image.convert('RGB').save(buf, 'JPEG', quality=90)
        schema = {'type': 'object', 'required': list(self.labels),
                  'properties': {g: {'type': 'string', 'enum': opts} for g, opts in self.labels.items()}}
        body = {'model': self.model, 'stream': False, 'format': schema, 'options': {'temperature': 0},
                'messages': [{'role': 'user', 'images': [base64.b64encode(buf.getvalue()).decode()],
                              'content': 'This is a cropped surveillance image of one person. For every field pick the value '
                                         'that best describes this person. Answer only with the JSON object.'}]}
        return json.loads(self.http.post('/api/chat', json=body).json()['message']['content'])


def matches(pred, truth, group, tolerant):
    if isinstance(pred, frozenset):
        return truth in pred
    if pred == truth:
        return True
    return tolerant and group in COLOR_GROUPS and any(pred in f and truth in f for f in FAMILIES)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--models', default='clip,pulc,qwen')
    ap.add_argument('--limit', type=int, default=1000)
    ap.add_argument('--qwen-limit', type=int, default=100)
    ap.add_argument('--qwen-model', default='qwen2.5vl:3b')
    ap.add_argument('--clip-arch', default='ViT-B-32')
    args = ap.parse_args()
    rows, labels = load(args.limit)
    RESULTS.mkdir(exist_ok=True)
    print(f'{len(rows)} images; groups: {", ".join(labels)}')

    makers = {'clip': lambda: Clip(labels, args.clip_arch), 'pulc': lambda: Pulc(labels), 'qwen': lambda: Qwen(labels, args.qwen_model)}
    report = {}
    for key in args.models.split(','):
        model = makers[key]()
        subset = rows[:args.qwen_limit] if key == 'qwen' else rows
        stats, times = defaultdict(Counter), []
        with (RESULTS / f'{model.name.replace(":", "_")}.jsonl').open('w', encoding='utf-8') as log:
            for n, r in enumerate(subset, 1):
                image = Image.open(SAMPLE / 'images' / r['image'])
                start = time.perf_counter()
                try:
                    pred = model.predict(image)
                except Exception as exc:
                    print('  failed on', r['image'], exc)
                    continue
                times.append(time.perf_counter() - start)
                for group, truth in r['truth'].items():
                    if truth is None or group not in pred:
                        continue
                    stats[group]['n'] += 1
                    stats[group]['strict'] += matches(pred[group], truth, group, False)
                    stats[group]['tolerant'] += matches(pred[group], truth, group, True)
                log.write(json.dumps({'image': r['image'], 'truth': r['truth'],
                                      'pred': {g: sorted(v) if isinstance(v, frozenset) else v for g, v in pred.items()}}, ensure_ascii=False) + '\n')
                if n % 100 == 0:
                    print(f'  {model.name}: {n}/{len(subset)}')
        warm = times[3:] or times       # first calls include model warm-up
        report[model.name] = {'images': len(times), 'ms_per_image': round(1000 * sum(warm) / max(len(warm), 1)), 'groups': stats}
        print(f'{model.name}: {len(times)} images, {report[model.name]["ms_per_image"]} ms/image')

    majority = {}
    for group in labels:
        truths = Counter(r['truth'].get(group) for r in rows if r['truth'].get(group))
        if truths:
            label, count = truths.most_common(1)[0]
            majority[group] = (label, count / sum(truths.values()), sum(truths.values()))

    lines = ['# Model agreement with Active Guard people attributes', '',
             f'Sample: {len(rows)} best shots (Qwen: first {args.qwen_limit}). Cell = strict agreement '
             '(colour groups: strict / neighbouring colours accepted). "—" = model cannot output this attribute.', '',
             '| Attribute | n | Always guess most common | ' + ' | '.join(report) + ' |',
             '|---|---|---|' + '---|' * len(report)]
    for group, (label, share, n) in majority.items():
        cells = []
        for name, r in report.items():
            s = r['groups'].get(group)
            if not s or not s['n']:
                cells.append('—')
                continue
            strict = f"{100 * s['strict'] / s['n']:.0f}%"
            cells.append(strict + (f" / {100 * s['tolerant'] / s['n']:.0f}%" if group in COLOR_GROUPS else ''))
        lines.append(f'| {group} | {n} | {100 * share:.0f}% ({label}) | ' + ' | '.join(cells) + ' |')
    lines += ['', '| Model | Images | ms / image (CPU, after warm-up) |', '|---|---|---|']
    lines += [f"| {name} | {r['images']} | {r['ms_per_image']} |" for name, r in report.items()]
    (RESULTS / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
