"""Pinned four-channel streaming ISP generation, independent from stimulus/runtime."""
import hashlib
import json
from pathlib import Path
import re
import shutil

from .runtime import ROOT, VENDOR, digest, vendor_manifest

PIPELINE_ID = 'adas-camera-4ch/1'
SOURCE_NAMES = ['blc.sv', 'colorgain.sv', 'demosaic.sv', 'linebuffer.sv',
                'simple_dual_ported_ram.sv', 'skidbuffer.sv']


def _streaming_demosaic():
    """Retain upstream arithmetic; isolate the long ready chain from line RAM.

    This is an explicitly named derivative, never an edit to the pinned vendor
    file. Its source and the original are both retained in every design bundle.
    """
    text = (VENDOR/'demosaic.sv').read_text()
    text = text.replace('module demosaic\n', 'module axiom_adas_demosaic\n', 1)
    text, count = re.subn(r'\blinebuffer #\(', 'axiom_adas_linebuffer #(', text)
    if count != 1:
        raise ValueError('Pinned demosaic linebuffer instance changed')
    marker = '  // instantiate the line buffer\n'
    if text.count(marker) != 1:
        raise ValueError('Pinned demosaic structure changed before ready-stage insertion')
    declarations = '''  // AXIOM derivative: registered-ready boundary after line storage.
  wire [LINEBUF_DATA_WIDTH-1:0] raw_linebuf_data;
  wire raw_linebuf_valid,raw_linebuf_ready,raw_linebuf_last;
  wire [TUSER_WIDTH-1:0] raw_linebuf_user;
  reg [LINEBUF_DATA_WIDTH-1:0] registered_line_data;
  reg registered_line_valid,registered_line_last;
  reg [TUSER_WIDTH-1:0] registered_line_user;
  wire registered_line_ready;
'''
    text = text.replace(marker, declarations+marker)
    for original, replacement in [('linebuf_tdata','raw_linebuf_data'),('linebuf_tvalid','raw_linebuf_valid'),
        ('linebuf_tready','raw_linebuf_ready'),('linebuf_tlast','raw_linebuf_last'),('linebuf_tuser','raw_linebuf_user')]:
        text, count = re.subn(r'(\.m_axis_\w+\s*\()'+original+r'(\))',
                             lambda m: m[1]+replacement+m[2], text)
        if count != 1:
            raise ValueError('Pinned linebuffer connection changed: '+original)
    marker = '  assign linebuf_tready = pipe_0_tready | ~conv_tvalid;'
    stage = '''  // Registered elastic EBR outputs before the transparent ready skid.
  assign raw_linebuf_ready=!registered_line_valid||registered_line_ready;
  always_ff @(posedge clk) begin
    if(raw_linebuf_ready) begin
      registered_line_data<=raw_linebuf_data;
      registered_line_valid<=raw_linebuf_valid;
      registered_line_last<=raw_linebuf_last;
      registered_line_user<=raw_linebuf_user;
    end
    if(!rstn) registered_line_valid<=0;
  end
  skidbuffer #(.DATA_WIDTH(LINEBUF_DATA_WIDTH),.TUSER_WIDTH(TUSER_WIDTH)) line_ready_boundary (
    .clk(clk),.rstn(rstn),
    .s_axis_tdata(registered_line_data),.s_axis_tvalid(registered_line_valid),.s_axis_tready(registered_line_ready),
    .s_axis_tlast(registered_line_last),.s_axis_tuser(registered_line_user),
    .m_axis_tdata(linebuf_tdata),.m_axis_tvalid(linebuf_tvalid),.m_axis_tready(linebuf_tready),
    .m_axis_tlast(linebuf_tlast),.m_axis_tuser(linebuf_tuser));
'''
    if text.count(marker) != 1:
        raise ValueError('Pinned convolution ready boundary changed')
    return '// AXIOM derivative: extra ready pipeline; upstream MIT notices retained.\n'+text.replace(marker,stage+marker)


def _pipelined_colorgain():
    """Move saturation into the existing stage 3; retain upstream arithmetic."""
    text = (VENDOR/'colorgain.sv').read_text()
    text = text.replace('module colorgain\n', 'module axiom_adas_colorgain\n', 1)
    text = text.replace('  reg [DATA_WIDTH-1:0]  pipe_2_tdata;',
                        '  localparam PRODUCT_WIDTH=PIXEL_PER_CYCLE*(PIXEL_BIT_WIDTH+16);\n'
                        '  reg [PRODUCT_WIDTH-1:0] pipe_2_tdata;', 1)
    text = text.replace('  reg [DATA_WIDTH-1:0] multiplier_output;',
                        '  reg [PRODUCT_WIDTH-1:0] multiplier_output;', 1)
    def product(match):
        index = int(match[1])
        return (f'multiplier_output[{index}*(PIXEL_BIT_WIDTH+16)+:PIXEL_BIT_WIDTH+16] = '
                f'pipe_1_pixel_{index} * {match[2]};')
    text, count = re.subn(r'multiplier_output\[[^\]]+\] = shift_and_saturate\(pipe_1_pixel_(\d) \* (pipe_1_pixel_\d_gain)\);',
                         product, text)
    if count != 7:
        raise ValueError('Pinned gain multiplier structure changed')
    marker = '      pipe_3_tdata   <= pipe_2_tdata;'
    replacement = """      // AXIOM derivative: full product registered in stage 2; saturation
      // uses the existing stage 3, with unchanged valid/ready and metadata.
      pipe_3_tdata <= '0;
      for(integer pixel=0;pixel<PIXEL_PER_CYCLE;pixel=pixel+1)
        pipe_3_tdata[pixel*PIXEL_BIT_WIDTH+:PIXEL_BIT_WIDTH] <=
          shift_and_saturate(pipe_2_tdata[pixel*(PIXEL_BIT_WIDTH+16)+:PIXEL_BIT_WIDTH+16]);"""
    if text.count(marker) != 1:
        raise ValueError('Pinned gain output stage changed')
    return '// AXIOM derivative: product/saturation pipeline split; upstream MIT notices retained.\n'+text.replace(marker,replacement)


def _defined_blc():
    """Drive the subtraction's full unsigned input width in four-state RTL."""
    text=(VENDOR/'blc.sv').read_text().replace('module blc\n','module axiom_adas_blc\n',1)
    pattern=r'assign (pipe_0_pixel_\d_wire)\[PIXEL_BIT_WIDTH-1:0\] = (s_axis_tdata\[[^\]]+\]);'
    text,count=re.subn(pattern,lambda m: 'assign '+m[1]+" = {{(INT_DATA_WIDTH-PIXEL_BIT_WIDTH){1'b0}}, "+m[2]+'};',text)
    if count!=4:raise ValueError('Pinned BLC input extension structure changed')
    marker='  // pipeline stage 0\n'
    padding="""  // AXIOM: bus padding is defined, while active subtraction sees zero extension.
  if(DATA_WIDTH>PIXEL_PER_CYCLE*PIXEL_BIT_WIDTH) begin
    assign pipe_1_tdata_wire[DATA_WIDTH-1:PIXEL_PER_CYCLE*PIXEL_BIT_WIDTH]='0;
  end
"""
    if text.count(marker)!=1:raise ValueError('Pinned BLC stage structure changed')
    return '// AXIOM derivative: defined zero extension and output padding; MIT notices retained.\n'+text.replace(marker,padding+marker)


def _registered_linebuffer():
    """Delay all cascade writes together; retain the read/output schedule.

    The active width is at least eight pixels. Writes remain ahead of the next
    row's read of the same address, including continuous one-pixel/cycle input.
    Unconditional data registers isolate EBR outputs from the next RAM's input
    enable mux without adding a valid/ready stage to the image stream.
    """
    text=(VENDOR/'linebuffer.sv').read_text().replace('module linebuffer\n','module axiom_adas_linebuffer\n',1)
    marker='  genvar i;\n'
    stage='''  // AXIOM: registered cascade writes, aligned address/enable/data.
  reg [MEM_WIDTH-1:0] cascade_data [CONV_SIZE-2:0];
  reg [MEM_DEPTH-1:0] cascade_address;
  reg cascade_write;
  always_ff @(posedge clk) begin
    for(integer row=0;row<CONV_SIZE-1;row=row+1)
      cascade_data[row]<=line_data[row];
    cascade_address<=write_counter;
    cascade_write<=write_en;
    if(!rstn) cascade_write<=0;
  end
'''
    if text.count(marker)!=1:raise ValueError('Pinned linebuffer RAM structure changed')
    text=text.replace(marker,stage+marker)
    for old,new in [('(write_en)','(cascade_write)'),('(write_counter)','(cascade_address)'),('(line_data[i])','(cascade_data[i])')]:
        if text.count(old)!=1:raise ValueError('Pinned linebuffer write connection changed: '+old)
        text=text.replace(old,new)
    return '// AXIOM derivative: registered cascade RAM writes; MIT notices retained.\n'+text


def generated_design(config, outdir):
    """Internal small dimensions permit focused tests; API fixes the delivery target."""
    if config.get('engine', 'adas_module_rtl') != 'adas_module_rtl' or config.get('pipeline_id', PIPELINE_ID) != PIPELINE_ID:
        raise ValueError('Unsupported ADAS RTL engine or pipeline')
    width, height = config.get('width', 1920), config.get('height', 1080)
    if (type(width) is not int or type(height) is not int or not 8 <= width <= 1920
            or not 8 <= height <= 1080 or width % 2 or height % 2):
        raise ValueError('Streaming ISP requires even 8..1920 by 8..1080 dimensions')
    channels = config.get('channels')
    if channels is None:
        channels = [dict(channel=i, bayer_pattern='RGGB', black_level=64, gains=[128]*4) for i in range(4)]
    if len(channels) != 4 or sorted(c.get('channel', i) for i, c in enumerate(channels)) != list(range(4)):
        raise ValueError('Exactly four unique streaming ISP channels are required')
    channels = [c for _, c in sorted(enumerate(channels), key=lambda pair: pair[1].get('channel', pair[0]))]
    cfas = {'BGGR': 0, 'GBRG': 1, 'GRBG': 2, 'RGGB': 3}
    cfa_bits = black_bits = gain_bits = 0
    for i, channel in enumerate(channels):
        pattern = channel.get('bayer_pattern', 'RGGB')
        black = channel.get('black_level', 64)
        gains = channel.get('gains', [128]*4)
        if (pattern not in cfas or type(black) is not int or not 0 <= black <= 4095
                or len(gains) != 4 or any(type(v) is not int or not 0 <= v <= 65535 for v in gains)):
            raise ValueError('Invalid channel CFA, black level or Q7 gains')
        cfa_bits |= cfas[pattern] << (2*i)
        black_bits |= black << (16*i)
        for j, gain in enumerate(gains): gain_bits |= gain << (64*i+16*j)
    vendor = vendor_manifest()
    outdir = Path(outdir); outdir.mkdir(parents=True, exist_ok=True)
    replacements = dict(WIDTH=width, HEIGHT=height, CFAS=f'{cfa_bits:02x}',
                        BLACKS=f'{black_bits:016x}', GAINS=f'{gain_bits:064x}')
    top = (ROOT/'templates/adas_top.sv').read_text()
    for key, value in replacements.items(): top = top.replace('@'+key+'@', str(value))
    (outdir/'axiom_adas_top.sv').write_text(top)
    channel=(ROOT/'templates/adas_channel.sv').read_text()
    channel,count=re.subn(r'\bblc #\(','axiom_adas_blc #(',channel)
    if count!=1:raise ValueError('ADAS BLC instance changed')
    (outdir/'axiom_adas_channel.sv').write_text(channel)
    for name in SOURCE_NAMES: shutil.copyfile(VENDOR/name, outdir/name)
    (outdir/'axiom_adas_demosaic.sv').write_text(_streaming_demosaic())
    (outdir/'axiom_adas_colorgain.sv').write_text(_pipelined_colorgain())
    (outdir/'axiom_adas_blc.sv').write_text(_defined_blc())
    (outdir/'axiom_adas_linebuffer.sv').write_text(_registered_linebuffer())
    for name in ['LICENSE.md', 'REUSE.toml']: shutil.copyfile(VENDOR/name, outdir/('mini-isp-'+name))
    sources = [outdir/'axiom_adas_top.sv', outdir/'axiom_adas_channel.sv', outdir/'axiom_adas_demosaic.sv', outdir/'axiom_adas_colorgain.sv', outdir/'axiom_adas_blc.sv', outdir/'axiom_adas_linebuffer.sv', *[outdir/name for name in SOURCE_NAMES]]
    hashes = {p.name: digest(p) for p in sources}
    design_hash = hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    manifest = dict(schema_version='adas-generated-design/1', pipeline_id=PIPELINE_ID,
        top='axiom_adas_top', design_hash=design_hash, sources=hashes, vendor=vendor,
        width=width, height=height, channels=4, clock_hz=100_000_000, pixels_per_cycle_per_channel=1,
        input='Four nonbackpressurable RAW12 logical camera streams; no PHY/CDC',
        output='Four independent ready/valid RGB12 streams with terminal commit/abort',
        roi=dict(x=2,y=2,width=width-4,height=height-4), ingress_fifo_beats=4096,
        ingress_fifo_accounting='RAM plus prefetched read beat; excludes one-beat ISP ingress and downstream pipeline',
        isp_ingress_register_beats=1,
        line_ram_bits=4*4*(1 << (width-1).bit_length())*12,
        derivative=dict(file='axiom_adas_demosaic.sv', original='demosaic.sv',
                        original_sha256=digest(VENDOR/'demosaic.sv'),
                        change='Registered elastic EBR outputs and ready skid after linebuffer; arithmetic and CFA unchanged'),
        gain_derivative=dict(file='axiom_adas_colorgain.sv', original='colorgain.sv',
            original_sha256=digest(VENDOR/'colorgain.sv'),
            change='Register full product in stage 2 and saturate in existing stage 3; unchanged latency, arithmetic and CFA'),
        blc_derivative=dict(file='axiom_adas_blc.sv',original='blc.sv',original_sha256=digest(VENDOR/'blc.sv'),
            change='Explicit zero extension before 17-bit subtraction and defined output padding; fixes valid-data X propagation hidden by two-state simulation'),
        linebuffer_derivative=dict(file='axiom_adas_linebuffer.sv',original='linebuffer.sv',original_sha256=digest(VENDOR/'linebuffer.sv'),
            change='Register cascade write data/address/enable together; unchanged read/output schedule and one-pixel/cycle throughput'),
        control_pipeline=dict(fault_detection_stages=1, terminal_retirement_cycles=1,
            partial_frame_policy='All RGB remains provisional until terminal commit; abort discards the complete frame'),
        reset_policy='Abort admitted partial frame; discard until next SOF; no source replay',
        csr_schema='axiom-adas-csr/1', event_queue_depth=4,
        csr=dict(channel_stride=256, control=0, black_level_shadow=4,
                 gains_shadow=[8,12,16,20], commit=24, status=28,
                 admitted_frames=32, committed_frames=36, aborted_frames=40,
                 overflow_count=44, framing_errors=48, input_count=52,
                 output_count=56, unadmitted_frames=60, fifo_level=64,
                 fifo_peak=68, epoch=72, config_version=76,
                 global_identity=4092, control_bits={'enable':0,'reset_pulse':1},
                 commit_policy='Write bit0 to snapshot shadows, then apply at next admitted SOF; a second pending commit is rejected'),
        event_scope='One terminal event per admitted frame; full queue blocks admission',
        scope='Open ISP FPGA DUT; not Qualcomm equivalence, camera PHY or physical HIL')
    (outdir/'design.json').write_text(json.dumps(manifest, indent=2)+'\n')
    return dict(sources=[str(p.resolve()) for p in sources], top='axiom_adas_top',
                design_hash=design_hash, manifest=manifest)
