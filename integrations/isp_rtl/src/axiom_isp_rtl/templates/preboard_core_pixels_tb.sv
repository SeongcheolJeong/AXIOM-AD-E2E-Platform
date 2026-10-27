`timescale 1ns/1ps
module preboard_core_pixels_tb;
 reg clk=0,reset=1;always #5 clk=~clk;
 reg [3:0] cv=0,cs=0,ce=0;
 reg [11:0] awaddr=0;reg awvalid=0,wvalid=0;
 wire awready,wready,bvalid;wire [1:0] bresp;
 wire [3:0] ov,ev,ek;wire [143:0] rgb;
 wire [63:0] epochs,versions;
 integer pixels[0:3],terminals[0:3],errors=0,c,x,y;
 axiom_adas_top dut(.clk(clk),.reset(reset),.cam_valid(cv),.cam_sof(cs),.cam_eol(ce),
  .cam_data({4{16'd1000}}),.cam_frame_id(64'd0),.out_valid(ov),.out_ready(4'd15),.out_data(rgb),
  .out_epoch(epochs),.out_config_version(versions),.event_valid(ev),.event_kind(ek),.event_ready(4'd15),
  .s_axi_awaddr(awaddr),.s_axi_awvalid(awvalid),.s_axi_awready(awready),
  .s_axi_wdata(32'd1),.s_axi_wstrb(4'd15),.s_axi_wvalid(wvalid),.s_axi_wready(wready),
  .s_axi_bresp(bresp),.s_axi_bvalid(bvalid),.s_axi_bready(1'b1),
  .s_axi_araddr(12'd0),.s_axi_arvalid(1'b0),.s_axi_rready(1'b1));
 always @(posedge clk) if(!reset) begin
  for(integer k=0;k<4;k=k+1) begin
   if(ov[k]) begin
    pixels[k]=pixels[k]+1;
    if((^rgb[k*36+:36])===1'bx) begin errors=errors+1;$display("CORE_VALID_DATA_X ch=%0d",k);end
    if(epochs[k*16+:16]!==0||versions[k*16+:16]!==0) errors=errors+1;
   end
   if(ev[k]) begin terminals[k]=terminals[k]+1;if(ek[k]!==1'b1) errors=errors+1;end
  end
 end
 task enable_channel(input integer channel);
  begin
   @(negedge clk);awaddr=channel*256;awvalid=1;wvalid=1;
   @(posedge clk);while(!awready||!wready) @(posedge clk);
   @(negedge clk);awvalid=0;wvalid=0;
   wait(bvalid);if(bresp!==0) errors=errors+1;@(negedge clk);
  end
 endtask
 initial begin
  for(c=0;c<4;c=c+1) begin pixels[c]=0;terminals[c]=0;end
  repeat(4) @(negedge clk);reset=0;
  for(c=0;c<4;c=c+1) enable_channel(c);
  for(y=0;y<16;y=y+1) for(x=0;x<16;x=x+1) begin
   @(negedge clk);cv=15;cs=(x==0&&y==0)?15:0;ce=(x==15)?15:0;
  end
  @(negedge clk);cv=0;cs=0;ce=0;
  repeat(500) @(negedge clk);
  for(c=0;c<4;c=c+1) if(pixels[c]!=144||terminals[c]!=1) errors=errors+1;
  $display("CORE_PIXELS_RESULT errors=%0d pixels=%0d,%0d,%0d,%0d",errors,pixels[0],pixels[1],pixels[2],pixels[3]);
  if(errors) $fatal(1,"core valid-data four-state check failed");$finish;
 end
 initial begin #100000;$fatal(1,"core pixel watchdog");end
endmodule
