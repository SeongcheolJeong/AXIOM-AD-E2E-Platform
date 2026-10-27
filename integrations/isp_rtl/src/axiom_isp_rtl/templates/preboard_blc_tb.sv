`timescale 1ns/1ps
`ifndef BLC_MODULE
`define BLC_MODULE axiom_adas_blc
`endif
module preboard_blc_tb;
 reg clk=0,rstn=0,valid=0,sof=0;reg [15:0] raw=0;
 always #5 clk=~clk;
 wire ready,ov;wire [15:0] data;
 integer count=0,errors=0;
 `BLC_MODULE #(.PIXEL_PER_CYCLE(1),.PIXEL_BIT_WIDTH(12)) dut(.clk(clk),.rstn(rstn),.black_level(16'd64),
  .s_axis_tdata(raw),.s_axis_tvalid(valid),.s_axis_tready(ready),.s_axis_tlast(1'b0),.s_axis_tuser(sof),
  .m_axis_tdata(data),.m_axis_tvalid(ov),.m_axis_tready(1'b1));
 always @(negedge clk) if(ov) begin
  if((^data[11:0])===1'bx) begin errors=errors+1;$display("BLC_VALID_DATA_X");end
  if(data[11:0]!==((count==0)?12'd936:(count==1)?12'd0:12'd4031)) errors=errors+1;
  count=count+1;
 end
 initial begin
  repeat(2) @(negedge clk);rstn=1;valid=1;sof=1;raw=1000;
  @(negedge clk);sof=0;raw=0;@(negedge clk);raw=4095;@(negedge clk);valid=0;
  repeat(8) @(negedge clk);
  $display("BLC_RESULT errors=%0d pixels=%0d",errors,count);
  if(errors||count!=3) $fatal(1,"BLC four-state regression");$finish;
 end
endmodule
