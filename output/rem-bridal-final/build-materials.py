"""将新生成绿幕动作转为透明素材，保留原视频并逐条记录技术检查。"""

from pathlib import Path
import hashlib
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
ANIMATIONS = ROOT / 'pet' / 'rem-bridal-animation'
ANIMATIONS.mkdir(parents=True, exist_ok=True)
(ROOT / 'review').mkdir(exist_ok=True)
KEY = 'format=rgba,colorkey=0x00ff00:0.3:0.08'
specs = json.loads((ROOT / 'action-plan.json').read_text())
report_file = ROOT / 'validation.json'
report = json.loads(report_file.read_text()) if report_file.exists() else {}


def ffmpeg(*arguments):
    """转码失败立即停止，避免把残缺文件当作完成素材。"""
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', *map(str, arguments)], check=True)


def bounds(pixels, width):
    """以 alpha 大于 32 的像素测量可见角色及道具范围。"""
    visible = [index for index, alpha in enumerate(pixels[3::4]) if alpha > 32]
    assert visible, '角色不可见'
    return [min(index % width for index in visible), min(index // width for index in visible),
            max(index % width for index in visible) + 1, max(index // width for index in visible) + 1]


actions = sys.argv[1:] or [spec['id'] for spec in specs]
for action in actions:
    source = ROOT / 'source' / (action + '.mp4')
    target = ANIMATIONS / (action + '.webm')
    if not source.exists() or (not sys.argv[1:] and action in report and target.exists()):
        continue
    probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(source)]))
    streams = probe['streams']
    assert len(streams) == 1 and streams[0]['codec_type'] == 'video', '预期无声视频'
    width, height = streams[0]['width'], streams[0]['height']
    first = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(source), '-vf', KEY,
                                     '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgba', '-'])
    left, top, right, bottom = bounds(first, width)
    factor = 267 / (bottom - top)
    scaled_width = round(width * factor / 2) * 2
    scaled_height = round(height * factor / 2) * 2
    offset_x = round(320 - (left + right) / 2 * scaled_width / width)
    offset_y = round(330 - bottom * scaled_height / height)
    # 仅按首帧计算固定变换，不逐帧追踪缩放，避免道具入场时人物突然缩小。
    filters = (f'[0:v]{KEY},scale={scaled_width}:{scaled_height}:flags=lanczos,fps=24[sprite];'
               f'[1:v][sprite]overlay=x={offset_x}:y={offset_y}:format=auto,format=yuva420p[out]')
    ffmpeg('-i', source, '-f', 'lavfi', '-i', 'color=c=black@0:s=640x360:r=24,format=rgba',
           '-filter_complex', filters, '-map', '[out]', '-t', '8', '-an', '-c:v', 'libvpx-vp9',
           '-pix_fmt', 'yuva420p', '-auto-alt-ref', '0', '-crf', '24', '-b:v', '0', target)
    ffmpeg('-i', source, '-vf', "select='eq(n,0)+eq(n,48)+eq(n,96)+eq(n,191)',scale=320:-1,tile=4x1",
           '-frames:v', '1', ROOT / 'review' / (action + '.jpg'))
    decoded = subprocess.check_output(['ffmpeg', '-v', 'error', '-c:v', 'libvpx-vp9', '-i', str(target),
                                       '-f', 'rawvideo', '-pix_fmt', 'rgba', '-'])
    frame_size = 640 * 360 * 4
    assert len(decoded) == 192 * frame_size, '输出不是8秒24fps'
    frames = [decoded[index:index + frame_size] for index in range(0, len(decoded), frame_size)]
    boxes = [bounds(frame, 640) for frame in frames]
    box = [min(item[0] for item in boxes), min(item[1] for item in boxes),
           max(item[2] for item in boxes), max(item[3] for item in boxes)]
    assert box[0] > 0 and box[1] > 0 and box[2] < 640 and box[3] < 360, '角色或道具触碰画布边缘'
    assert all(frame[3] == 0 for frame in frames), '背景左上角不透明'
    report[action] = {'frames': 192, 'canvas': [640, 360], 'allFrameBounds': box,
                      'distinctFrames': len({hashlib.sha256(frame).digest() for frame in frames}),
                      'firstLastMeanRgbaDifference': round(sum(abs(a - b) for a, b in zip(frames[0], frames[-1])) / frame_size, 3),
                      'visualReview': 'pending', 'note': '数值检查不等于动作、手部或循环衔接的人审通过。'}
    report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(action, json.dumps(report[action], ensure_ascii=False), flush=True)
