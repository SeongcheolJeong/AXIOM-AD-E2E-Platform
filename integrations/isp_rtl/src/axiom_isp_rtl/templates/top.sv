// AXIOM integration wrapper. Upstream blocks retain their MIT notices.
`timescale 1ns/1ns
module axiom_isp_top (
 input wire clk, reset,
 input wire in_valid, input wire [15:0] in_data,
 input wire in_sof, in_eol,
 output wire in_ready,
 output wire out_valid, output wire [35:0] out_data,
 output wire out_sof, out_eol, input wire out_ready,
 output wire [11:0] blc_data, wb_data,
 output wire blc_valid, blc_ready, wb_valid, wb_ready,
 output wire [31:0] buffer_level,
 output wire [1:0] buffer_state
);
 localparam integer WIDTH=@WIDTH@, HEIGHT=@HEIGHT@;
 localparam integer FRAME_WORDS=WIDTH*HEIGHT, OUT_WORDS=(WIDTH-4)*(HEIGHT-4);
 localparam integer CFA=@CFA@;
 wire rstn=~reset;
 reg [31:0] input_count=0, output_count=0;
 wire input_gate=(input_count<FRAME_WORDS) && !reset;
 wire frame_done=out_valid && out_ready && (output_count==OUT_WORDS-1) && !reset;
 wire blc_in_ready;
 assign in_ready=blc_in_ready && input_gate;
 always @(posedge clk) begin
   if (reset || frame_done) begin input_count<=0; output_count<=0; end
   else begin
     if(in_valid && in_ready) input_count<=input_count+1;
     if(out_valid && out_ready) output_count<=output_count+1;
   end
 end
 wire [15:0] blc_bus,wb_bus;
 wire blc_sof,blc_eol,wb_sof,wb_eol;
 blc #(.PIXEL_PER_CYCLE(1),.PIXEL_BIT_WIDTH(12)) u_blc (
  .clk(clk),.rstn(rstn),.black_level(16'd@BLACK@),
  .s_axis_tvalid(in_valid && input_gate),.s_axis_tready(blc_in_ready),
  .s_axis_tdata({4'b0,in_data[11:0]}),.s_axis_tuser(in_sof),.s_axis_tlast(in_eol),
  .m_axis_tvalid(blc_valid),.m_axis_tready(blc_ready),.m_axis_tdata(blc_bus),
  .m_axis_tuser(blc_sof),.m_axis_tlast(blc_eol));
 colorgain #(.CFA_ORIENTATION(CFA),.PIXEL_PER_CYCLE(1),.PIXEL_BIT_WIDTH(12)) u_wb (
  .clk(clk),.rstn(rstn),.rgain(16'd@RGAIN@),.bgain(16'd@BGAIN@),
  .g0gain(16'd@G0GAIN@),.g1gain(16'd@G1GAIN@),
  .s_axis_tvalid(blc_valid),.s_axis_tready(blc_ready),.s_axis_tdata(blc_bus),
  .s_axis_tuser(blc_sof),.s_axis_tlast(blc_eol),
  .m_axis_tvalid(wb_valid),.m_axis_tready(wb_ready),.m_axis_tdata(wb_bus),
  .m_axis_tuser(wb_sof),.m_axis_tlast(wb_eol));
 assign blc_data=blc_bus[11:0]; assign wb_data=wb_bus[11:0];

 // Inferred synchronous frame RAM. No vendor-specific primitive.
 // No next-frame input is accepted until the previous RGB frame is drained.
 localparam integer ADDR_BITS=$clog2(FRAME_WORDS);
 reg [11:0] frame_ram [0:FRAME_WORDS-1];
 reg [ADDR_BITS-1:0] write_index=0,read_index=0;
 reg [1:0] state=0; // capture, replay, drain RGB
 reg replay_valid=0;
 reg [11:0] replay_data;
 reg replay_sof,replay_eol;
 wire replay_ready;
 assign wb_ready=(state==0) && !reset;
 assign buffer_state=state;
 assign buffer_level=state==0 ? 32'(write_index) : state==1 ? FRAME_WORDS-32'(read_index) : 0;
 always @(posedge clk) begin
   if (wb_valid && wb_ready) frame_ram[write_index]<=wb_bus[11:0];
 end
 always @(posedge clk) begin
   if(reset || frame_done) begin
     state<=0; write_index<=0; read_index<=0; replay_valid<=0;
     replay_sof<=0; replay_eol<=0;
   end else begin
     if(state==0 && wb_valid && wb_ready) begin
       if(32'(write_index)==FRAME_WORDS-1) begin state<=1; read_index<=0; end
       else write_index<=write_index+1'b1;
     end
     if(state==1 && (!replay_valid || replay_ready)) begin
       replay_data<=frame_ram[read_index]; replay_valid<=1;
       replay_sof<=(read_index==0); replay_eol<=((32'(read_index)%WIDTH)==WIDTH-1);
       if(32'(read_index)==FRAME_WORDS-1) state<=2;
       else read_index<=read_index+1'b1;
     end else if(state==2 && replay_valid && replay_ready) replay_valid<=0;
   end
 end
 wire [39:0] mhc_data;
 demosaic #(.CFA_ORIENTATION(CFA),.PIXEL_PER_CYCLE(1),.PIXEL_BIT_WIDTH(12),
             .MAX_RESOLUTION(@MAX_RES@)) u_mhc (
  .clk(clk),.rstn(rstn),.s_axis_tvalid(replay_valid),.s_axis_tready(replay_ready),
  .s_axis_tdata({4'b0,replay_data}),.s_axis_tuser(replay_sof),.s_axis_tlast(replay_eol),
  .m_axis_tvalid(out_valid),.m_axis_tready(out_ready),.m_axis_tdata(mhc_data),
  .m_axis_tuser(out_sof),.m_axis_tlast(out_eol));
 // Upstream low-to-high G,B,R lanes -> conventional packed R,G,B.
 assign out_data={mhc_data[35:24],mhc_data[11:0],mhc_data[23:12]};
endmodule
