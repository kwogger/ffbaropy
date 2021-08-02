#! python
import chevron
import collections
import ffmpeg
import math
import re
import srt
import struct

INPUT_PATH = 'bee.mp4'
PROOF_PATH = 'proof.mp4'
DICT_PATTERN_PATH = 'dict.txt'
ITEM_ASSEMBLY_TEMPLATE = 'oled.mustache'
SUBTITLE_PATH = 'bee.srt'

WIDTH = 32
HEIGHT = 18
FRAMERATE = 8

COMPONENT_ID_OFFSET = 62
MAX_FRAMES = 16777216
LIGHT_X_OFFSET = -192
LIGHT_Y_OFFSET = 103
REGEX_X_OFFSET = -192
REGEX_Y_OFFSET = 103
RESERVED_REGEX_CHARS = '.+*?^$()[]{}|\\'
RESERVED_XML_CHARS = '<>&"\''

ALPHABET = [chr(i) for i in range(32, 127) if chr(i) not in RESERVED_XML_CHARS]
BUFFER_SIZE = FRAMERATE * WIDTH * HEIGHT * 60
FRAME_SIZE = WIDTH * HEIGHT

template = {
    'filename': 'oled_%s' % (re.match('^(.*)\\..{3}$', INPUT_PATH).group(1),),
    'item_description': (
        'A %dx%d OLED screen with a video generated from %s'
        % (WIDTH, HEIGHT, INPUT_PATH)),
    'frame_rate': FRAMERATE,
    'frame_length': FRAME_SIZE,
    'frame_pattern': '^(?<out>.{%d})' % FRAME_SIZE,
    'frame_remover_pattern': '^.{%d}(?<out>.+)' % FRAME_SIZE,
    'buffer_length': BUFFER_SIZE,
    'buffer_pattern': '^(?<out>.{%d,%d})' % (FRAME_SIZE, BUFFER_SIZE),
    'buffer_remover_pattern': '^.{%d}(?<out>.+)' % BUFFER_SIZE,
    'frame_empty_pattern': '^.{%d}$' % FRAME_SIZE,
    'caption_enabled': SUBTITLE_PATH is not None,
}

relays = []
regexs = []
lights = []
wires = []


class Node:
    def __init__(self, comp, name):
        self.comp = comp
        self.name = name
        self.wire_id = None

    def x_coord(self):
        return self.comp.x_coord + self.comp.comp_width / 2

    def y_coord(self):
        return self.comp.y_coord - self.comp.comp_height / 2

    def template(self):
        return {'id': self.wire_id} if self.wire_id is not None else None


class Wire:
    def __init__(self, node_start, node_end):
        self.node_start = node_start
        self.node_end = node_end
        self.id = None

    def template(self):
        return {
            'id': self.id,
            'rectxy': '{},{}'.format(
                (self.node_start.x_coord() + self.node_end.x_coord()) / 2,
                (self.node_start.y_coord() + self.node_end.y_coord()) / 2),
            'nodes': '{};{};{};{}'.format(
                self.node_start.x_coord(),
                self.node_start.y_coord(),
                self.node_end.x_coord(),
                self.node_end.y_coord()),
        }


class Component:
    def __init__(self, x_coord, y_coord, comp_width, comp_height):
        self.x_coord = x_coord
        self.y_coord = y_coord
        self.comp_width = comp_width
        self.comp_height = comp_height
        self.id = None

    def node(self, name):
        return Node(self, name)

    def template_data(self):
        return {
            'id': self.id,
            'rectxy': f'{self.x_coord},{self.y_coord}',
        }


class Concat(Component):
    def __init__(self):
        super().__init__(-224, 71, 15, 14)
        self.signal_out = [self.node('signal_out') for _ in range(5)]

    def template(self):
        return [out.template() for out in self.signal_out]


class Relay(Component):
    def __init__(self):
        super().__init__(-207, 88, 15, 13)
        self.signal_in1 = self.node('signal_in1')
        self.signal_in2 = self.node('signal_in2')
        self.signal_out1 = [self.node('signal_out1') for _ in range(5)]
        self.signal_out2 = [self.node('signal_out2') for _ in range(5)]

    def template(self):
        template = super().template_data()
        template['in1'] = self.signal_in1.template()
        template['in2'] = self.signal_in2.template()
        template['out1'] = [
            node.template()
            for node in self.signal_out1
            if node.wire_id is not None]
        template['out2'] = [
            node.template()
            for node in self.signal_out2
            if node.wire_id is not None]
        return template


class RegEx(Component):
    def __init__(self, x, y):
        super().__init__(
            REGEX_X_OFFSET + x * 16,
            REGEX_Y_OFFSET + (HEIGHT - 1 - y) * 16,
            15,
            13)
        self.x = x
        self.y = y
        self.signal_in = self.node('signal_in')
        self.signal_out = self.node('signal_out')

    def pattern(self):
        px_offset = self.x + self.y * WIDTH
        lib_offset = WIDTH * HEIGHT - px_offset - 1
        return ('^'
                + ('' if px_offset == 0 else '(?:.{%s})' % px_offset)
                + '(?<px>.)'
                + ('' if lib_offset == 0 else '(?:.{%s})' % lib_offset)
                + '(?:.{8})*?\\k<px>(?<out>.{7})')

    def template(self):
        template = super().template_data()
        template['in'] = self.signal_in.template()
        template['out'] = self.signal_out.template()
        template['pattern'] = self.pattern()
        return template


class Light(Component):
    def __init__(self, x, y):
        super().__init__(
            LIGHT_X_OFFSET + x * 16,
            LIGHT_Y_OFFSET + (HEIGHT - 1 - y) * 16,
            16,
            16)
        self.set_color = self.node('set_color')

    def template(self):
        template = super().template_data()
        template['in'] = self.set_color.template()
        return template


print('> Processing video')
input_streams = (
    ffmpeg.input(INPUT_PATH)
          .filter('fps', fps=FRAMERATE)
          .filter('scale', WIDTH, HEIGHT, flags='lanczos')
          .split()
)

palettegen = input_streams[0].filter(
    'palettegen',
    max_colors=len(ALPHABET),
    reserve_transparent=False)

paletteuse = ffmpeg.filter(
    [input_streams[1].filter('fifo'), palettegen],
    filter_name='paletteuse',
    dither='floyd_steinberg'
).split()

output = ffmpeg.merge_outputs(
    paletteuse[0].output('pipe:', format='rawvideo', pix_fmt='rgb24'),
    paletteuse[1].output(
        PROOF_PATH,
        vcodec='libx264',
        preset='veryslow',
        crf=0)
).overwrite_output()

print(output.compile())
rgb_raw, _ = output.run(capture_stdout=True)

print('> Generating character mapping')
data = struct.iter_unpack('BBB', rgb_raw)
px_dict = dict(
    zip([px for px, _ in collections.Counter(data).most_common()], ALPHABET))

print('> Writing video data')
template['vid_data'] = ''.join(
    [px_dict[px] for px in struct.iter_unpack('BBB', rgb_raw)])
template['vid_data_length'] = len(template['vid_data'])
assert len(template['vid_data']) <= (MAX_FRAMES * WIDTH * HEIGHT)

print('> Writing dictionary')
template['px_lib'] = ''.join(
    [char
        + '#'
        + ''.join(['{:02x}'.format(byte) for byte in px])
        for px, char in px_dict.items()]
)
template['px_lib_length'] = len(template['px_lib'])

if SUBTITLE_PATH is not None:
    print('> Parsing subtitles')
    with open(SUBTITLE_PATH, 'r+') as f:
        subtitle_data = srt.parse(f.read(), ignore_errors=True)
    template['caption_data'] = '~' + ''.join(['%d %s~' % (
        math.floor(subtitle.start.total_seconds() * FRAMERATE),
        text
    )
        for subtitle in subtitle_data
        for text in subtitle.content.replace('~', '-').split('\n')
    ])
    template['caption_data_length'] = len(template['caption_data'])


print('> Generating components and wiring')

concat = Concat()
out_nodes = collections.deque(concat.signal_out)

while len(out_nodes) < WIDTH * HEIGHT:
    relay = Relay()
    wires.append(Wire(out_nodes.popleft(), relay.signal_in1))
    out_nodes.extend(relay.signal_out1)
    if len(out_nodes) < WIDTH * HEIGHT:
        wires.append(Wire(out_nodes.popleft(), relay.signal_in2))
        out_nodes.extend(relay.signal_out2)
    relays.append(relay)

for y in range(HEIGHT):
    for x in range(WIDTH):
        regex = RegEx(x, y)
        light = Light(x, y)
        wires.append(Wire(out_nodes.popleft(), regex.signal_in))
        wires.append(Wire(regex.signal_out, light.set_color))
        regexs.append(regex)
        lights.append(light)


for i in range(len(relays)):
    relays[i].id = COMPONENT_ID_OFFSET + i

for i in range(len(regexs)):
    regexs[i].id = COMPONENT_ID_OFFSET + len(relays) + i

for i in range(len(lights)):
    lights[i].id = COMPONENT_ID_OFFSET + len(relays) + len(regexs) + i

offset_id = COMPONENT_ID_OFFSET + len(relays) + len(regexs) + len(lights)
for i in range(len(wires)):
    wires[i].id = offset_id + i
    wires[i].node_start.wire_id = wires[i].id
    wires[i].node_end.wire_id = wires[i].id

template['concat_length'] = \
    template['frame_length'] + template['px_lib_length']
template['concat_out'] = concat.template()
template['relay_comp'] = [relay.template() for relay in relays]
template['regex_comp'] = [regex.template() for regex in regexs]
template['light_comp'] = [light.template() for light in lights]
template['wire'] = [wire.template() for wire in wires]

print('> Writing item assembly')
with open('build/%s.xml' % template['filename'], 'w') as f_out:
    with open(ITEM_ASSEMBLY_TEMPLATE, 'r') as f_template:
        f_out.write(chevron.render(f_template, template))

print('> Done')
