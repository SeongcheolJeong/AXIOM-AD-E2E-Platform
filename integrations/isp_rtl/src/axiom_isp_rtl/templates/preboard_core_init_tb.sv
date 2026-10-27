`timescale 1ns/1ps
module preboard_core_init_tb;
 reg clk=0,reset=0;always #5 clk=~clk;
 wire [3:0] ov,ev,active;wire [63:0] level,version;
 wire [127:0] inputs,outputs;
 wire bv,rv,awready,wready,arready;wire [1:0] br,rr;
 axiom_adas_top dut(.clk(clk),.reset(reset),.cam_valid(4'd0),.cam_sof(4'd0),.cam_eol(4'd0),
  .cam_data(64'd0),.cam_frame_id(64'd0),.out_valid(ov),.out_ready(4'd15),
  .event_valid(ev),.event_ready(4'd15),.channel_active(active),.fifo_levels(level),
  .accepted_inputs(inputs),.accepted_outputs(outputs),.active_config_versions(version),
  .s_axi_awaddr(12'd0),.s_axi_awvalid(1'b0),.s_axi_awready(awready),
  .s_axi_wdata(32'd0),.s_axi_wstrb(4'd0),.s_axi_wvalid(1'b0),.s_axi_wready(wready),
  .s_axi_bresp(br),.s_axi_bvalid(bv),.s_axi_bready(1'b1),
  .s_axi_araddr(12'd0),.s_axi_arvalid(1'b0),.s_axi_arready(arready),
  .s_axi_rresp(rr),.s_axi_rvalid(rv),.s_axi_rready(1'b1));
 integer errors=0,unknown_before=0;
 task checked;
  begin
   if({ov,ev,active,level,inputs,outputs,version,bv,rv,br,rr}!==0) errors=errors+1;
   if({awready,wready,arready}!==3'b111) errors=errors+1;
  end
 endtask
 initial begin
  #1;if((^active)===1'bx) unknown_before=1;
  reset=1;repeat(4) @(negedge clk);checked();reset=0;
  repeat(8) begin @(negedge clk);checked();end
  reset=1;repeat(2) @(negedge clk);checked();reset=0;
  repeat(4) begin @(negedge clk);checked();end
  $display("CORE_INIT_RESULT errors=%0d startup_unknown=%0d control_samples=14",errors,unknown_before);
  if(errors||!unknown_before) $fatal(1,"core control initialization failed");$finish;
 end
endmodule
