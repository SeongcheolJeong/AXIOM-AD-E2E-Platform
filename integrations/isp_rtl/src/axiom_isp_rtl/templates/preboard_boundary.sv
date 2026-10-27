// Board-independent digital reference. No PLL, pin assignment or physical CDC proof.
module axiom_reset_boundary(input wire clk, por_n, pll_locked, output wire reset);
 (* ASYNC_REG = "TRUE" *) reg [1:0] release_sync;
 wire permit=por_n&&pll_locked;
 always @(posedge clk or negedge permit)
   if(!permit) release_sync<=0; else release_sync<={release_sync[0],1'b1};
 assign reset=!release_sync[1];
endmodule

// Eight-word, 32-bit Gray-pointer asynchronous FIFO reference. Both domains
// must share reset assertion; each reset deasserts on its own clock boundary.
// This block is not wired to the camera ISP and is not a source-synchronous PHY.
module axiom_boundary_fifo(
 input wire wclk,wrst,rclk,rrst,
 input wire wvalid,output wire wready,input wire [31:0] wdata,
 output wire rvalid,input wire rready,output wire [31:0] rdata);
 reg [31:0] memory[0:7];
 reg [3:0] wb,wg,rb,rg;
 (* ASYNC_REG = "TRUE" *) reg [3:0] rg1,rg2,wg1,wg2;
 reg full,empty;
 wire [3:0] wn=wb+{3'b0,(wvalid&&wready)},rn=rb+{3'b0,(rvalid&&rready)};
 wire [3:0] wgn=(wn>>1)^wn,rgn=(rn>>1)^rn;
 assign wready=!full&&!wrst;assign rvalid=!empty&&!rrst;assign rdata=memory[rb[2:0]];
 always @(posedge wclk or posedge wrst)
  if(wrst) begin wb<=0;wg<=0;rg1<=0;rg2<=0;full<=0;end
  else begin
   rg1<=rg;rg2<=rg1;wb<=wn;wg<=wgn;full<=(wgn=={~rg2[3:2],rg2[1:0]});
   if(wvalid&&wready) memory[wb[2:0]]<=wdata;
  end
 always @(posedge rclk or posedge rrst)
  if(rrst) begin rb<=0;rg<=0;wg1<=0;wg2<=0;empty<=1;end
  else begin wg1<=wg;wg2<=wg1;rb<=rn;rg<=rgn;empty<=(rgn==wg2);end
endmodule
