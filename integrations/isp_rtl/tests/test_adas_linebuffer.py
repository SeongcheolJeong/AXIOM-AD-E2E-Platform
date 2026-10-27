"""Four-state, cycle-exact cascade-write retiming against the pinned original."""
import shutil
import subprocess

import pytest

from axiom_isp_rtl.adas_design import _registered_linebuffer
from axiom_isp_rtl.runtime import VENDOR


@pytest.mark.parametrize('width',[8,16,1920])
def test_registered_writes_preserve_rows_and_stalls(tmp_path,width):
    iv=shutil.which('iverilog');vvp=shutil.which('vvp')
    if not iv or not vvp:pytest.skip('Icarus Verilog required for four-state comparison')
    (tmp_path/'derived.sv').write_text(_registered_linebuffer())
    instances=''
    for suffix,module in [('a','linebuffer'),('b','axiom_adas_linebuffer')]:
        instances+=f'''
wire ready_{suffix},valid_{suffix},last_{suffix},user_{suffix};wire [79:0] data_{suffix};
{module} #(.MAX_RESOLUTION(1<<$clog2(W)),.PIXEL_PER_CYCLE(1),.PIXEL_BIT_WIDTH(12)) {suffix}(
.clk(clk),.rstn(rstn),.s_axis_tdata(data),.s_axis_tvalid(valid),.s_axis_tready(ready_{suffix}),
.s_axis_tlast(last),.s_axis_tuser(user),.m_axis_tdata(data_{suffix}),.m_axis_tvalid(valid_{suffix}),
.m_axis_tready(ready),.m_axis_tlast(last_{suffix}),.m_axis_tuser(user_{suffix}));
'''
    tb=f'''module tb;
localparam W={width};reg clk=0;always #5 clk=~clk;
reg rstn=0,valid=0,ready=0,last=0,user=0,source_accepted=0;reg [15:0] data=0;
integer x=0,y=0,frame=0,cycles=0,accepted=0;reg [31:0] rng=32'h4281bead;
{instances}
initial begin repeat(3) @(negedge clk);rstn=1;end
always @(posedge clk) if(rstn) begin
  source_accepted=valid&&ready_a;
  if(ready_a!==ready_b||valid_a!==valid_b) $fatal(1,"handshake mismatch");
  if(valid_a) begin
    if(last_a!==last_b||user_a!==user_b) $fatal(1,"metadata mismatch");
    for(integer row=0;row<5;row=row+1)
      if(data_a[row*16+:12]!==data_b[row*16+:12]) $fatal(1,"pixel mismatch row=%0d cycle=%0d",row,cycles);
    if(ready) accepted=accepted+1;
  end
  if(valid&&ready_a) begin
    x=x+1;if(x==W) begin x=0;y=y+1;end
    if(y==12) begin y=0;frame=frame+1;end
  end
end
always @(negedge clk) if(rstn) begin
  cycles=cycles+1;rng={{rng[30:0],rng[31]^rng[21]^rng[1]^rng[0]}};
  // First frame continuously fills the minimum-width memory; later frames
  // inject bounded source gaps and independently held output beats.
  ready=(frame==0)||rng[3:0]!=0;
  if(!valid||source_accepted) valid=(frame<3)&&((frame==0)||rng[7:4]!=0);
  data=16'((x*37+y*61+frame*257)%4096);last=(x==W-1);user=(x==0&&y==0);
  if(frame==3&&cycles>W*40) begin
    if(accepted!=3*8*W) $fatal(1,"unexpected pixel count %0d",accepted);
    $display("LINEBUFFER_EQUIVALENT width=%0d pixels=%0d",W,accepted);$finish;
  end
  if(cycles>W*100) $fatal(1,"timeout");
end
endmodule
'''
    (tmp_path/'tb.sv').write_text(tb)
    result=subprocess.run([iv,'-g2012','-s','tb','-o',str(tmp_path/'sim'),str(tmp_path/'tb.sv'),
        str(tmp_path/'derived.sv'),str(VENDOR/'linebuffer.sv'),str(VENDOR/'simple_dual_ported_ram.sv')],
        capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    result=subprocess.run([vvp,str(tmp_path/'sim')],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'LINEBUFFER_EQUIVALENT' in result.stdout
