// AXIOM four-channel streaming ISP DUT, single 100 MHz internal clock.
// This is not a CSI receiver, Qualcomm implementation, or board-level interface.
module axiom_adas_top(
 input wire clk,reset,
 input wire [3:0] cam_valid,cam_sof,cam_eol,
 input wire [63:0] cam_data,cam_frame_id,
 output wire [3:0] out_valid,out_sof,out_eol,input wire [3:0] out_ready,
 output wire [143:0] out_data,
 output wire [63:0] out_frame_id,out_epoch,out_config_version,
 output wire [3:0] event_valid,event_kind,input wire [3:0] event_ready,
 output wire [31:0] event_reason,
 output wire [63:0] event_frame_id,event_epoch,event_config_version,
 output wire [63:0] fifo_levels,fifo_peaks,
 output wire [3:0] channel_active,
 output wire [127:0] accepted_inputs,accepted_outputs,
 output wire [63:0] active_config_versions,
 input wire [11:0] s_axi_awaddr,input wire s_axi_awvalid,output wire s_axi_awready,
 input wire [31:0] s_axi_wdata,input wire [3:0] s_axi_wstrb,input wire s_axi_wvalid,output wire s_axi_wready,
 output reg [1:0] s_axi_bresp,output reg s_axi_bvalid,input wire s_axi_bready,
 input wire [11:0] s_axi_araddr,input wire s_axi_arvalid,output wire s_axi_arready,
 output reg [31:0] s_axi_rdata,output reg [1:0] s_axi_rresp,output reg s_axi_rvalid,input wire s_axi_rready
);
 localparam integer WIDTH=@WIDTH@,HEIGHT=@HEIGHT@;
 localparam [7:0] CFAS=8'h@CFAS@;
 localparam [63:0] BLACKS=64'h@BLACKS@;
 localparam [255:0] GAINS=256'h@GAINS@;
 reg [3:0] enabled,local_resets,commits;
 reg [15:0] shadows [0:3][0:4];
 wire [3:0] pending;
 wire [63:0] epochs;
 wire [31:0] admitted [0:3],committed [0:3],aborted [0:3];
 wire [31:0] overflow [0:3],framing [0:3],unadmitted [0:3];
 genvar ch;
 generate for(ch=0;ch<4;ch=ch+1) begin:channels
  axiom_adas_channel #(.WIDTH(WIDTH),.HEIGHT(HEIGHT),.CFA(32'(CFAS[ch*2+:2])),
   .INIT_BLACK(32'(BLACKS[ch*16+:16])),.INIT_GAINS(GAINS[ch*64+:64])) core(
   .clk(clk),.reset(reset),.enabled(enabled[ch]),.local_reset(local_resets[ch]),.config_commit(commits[ch]),
   .shadow_black(shadows[ch][0]),.shadow_gains({shadows[ch][4],shadows[ch][3],shadows[ch][2],shadows[ch][1]}),
   .cam_valid(cam_valid[ch]),.cam_sof(cam_sof[ch]),.cam_eol(cam_eol[ch]),
   .cam_data(cam_data[ch*16+:16]),.cam_frame_id(cam_frame_id[ch*16+:16]),
   .out_valid(out_valid[ch]),.out_ready(out_ready[ch]),.out_sof(out_sof[ch]),.out_eol(out_eol[ch]),
   .out_data(out_data[ch*36+:36]),.out_frame_id(out_frame_id[ch*16+:16]),
   .out_epoch(out_epoch[ch*16+:16]),.out_config_version(out_config_version[ch*16+:16]),
   .event_valid(event_valid[ch]),.event_ready(event_ready[ch]),.event_kind(event_kind[ch]),
   .event_reason(event_reason[ch*8+:8]),.event_frame_id(event_frame_id[ch*16+:16]),
   .event_epoch(event_epoch[ch*16+:16]),.event_config_version(event_config_version[ch*16+:16]),
   .fifo_level(fifo_levels[ch*16+:16]),.fifo_peak(fifo_peaks[ch*16+:16]),
   .active(channel_active[ch]),.pending(pending[ch]),.epoch(epochs[ch*16+:16]),.config_version(active_config_versions[ch*16+:16]),
   .admitted_frames(admitted[ch]),.committed_frames(committed[ch]),.aborted_frames(aborted[ch]),
   .overflow_count(overflow[ch]),.framing_errors(framing[ch]),.unadmitted_frames(unadmitted[ch]),
   .input_count(accepted_inputs[ch*32+:32]),.output_count(accepted_outputs[ch*32+:32]));
 end endgenerate

 reg aw_pending,w_pending,execute_pending;
 reg [9:0] shadow_actions [0:3];
 reg [15:0] application_data [0:3];
 reg [3:0] enable_actions,reset_actions,commit_actions;
 reg [1:0] write_response;
 reg [11:0] write_address;
 reg [31:0] write_data;
 reg [3:0] write_strobe;
 assign s_axi_awready=!aw_pending&&!execute_pending&&!s_axi_bvalid;
 assign s_axi_wready=!w_pending&&!execute_pending&&!s_axi_bvalid;
 assign s_axi_arready=!s_axi_rvalid;
 wire [1:0] write_channel=write_address[9:8];
 wire [7:0] write_offset=write_address[7:0];
 wire [1:0] read_channel=s_axi_araddr[9:8];
 wire [7:0] read_offset=s_axi_araddr[7:0];
 // Masking written bytes is sufficient for range validation: every stored
 // shadow is already valid. Avoid reading a dynamically indexed shadow through
 // a mux, merging it, then routing that result back to all channel registers.
 wire [31:0] written_bits=write_data&{{8{write_strobe[3]}},{8{write_strobe[2]}},{8{write_strobe[1]}},{8{write_strobe[0]}}};
 integer i,j;
 always @(posedge clk) begin
  if(reset) begin
   enabled<=0;local_resets<=0;commits<=0;
   aw_pending<=0;w_pending<=0;execute_pending<=0;enable_actions<=0;reset_actions<=0;commit_actions<=0;write_response<=0;write_address<=0;write_data<=0;write_strobe<=0;
   s_axi_bvalid<=0;s_axi_bresp<=0;s_axi_rvalid<=0;s_axi_rresp<=0;s_axi_rdata<=0;
   for(i=0;i<4;i=i+1) begin
    shadow_actions[i]<=0;application_data[i]<=0;
    shadows[i][0]<=BLACKS[i*16+:16];
    for(j=1;j<5;j=j+1) shadows[i][j]<=GAINS[i*64+(j-1)*16+:16];
   end
  end else begin
   local_resets<=0;commits<=0;
   if(s_axi_awvalid&&s_axi_awready) begin write_address<=s_axi_awaddr;aw_pending<=1;end
   if(s_axi_wvalid&&s_axi_wready) begin write_data<=s_axi_wdata;write_strobe<=s_axi_wstrb;w_pending<=1;end
   if(s_axi_bvalid&&s_axi_bready) s_axi_bvalid<=0;
   // Per-channel registered actions isolate address/validation fanout from
   // shadow-register enables. Response is produced only when actions apply.
   if(execute_pending) begin
    execute_pending<=0;s_axi_bvalid<=1;s_axi_bresp<=write_response;
    local_resets<=reset_actions;commits<=commit_actions;
    for(i=0;i<4;i=i+1) begin
     if(enable_actions[i]) enabled[i]<=application_data[i][0];
     for(j=0;j<5;j=j+1) begin
      if(shadow_actions[i][j*2]) shadows[i][j][7:0]<=application_data[i][7:0];
      if(shadow_actions[i][j*2+1]) shadows[i][j][15:8]<=application_data[i][15:8];
     end
    end
   end
   // AW and W are independently captured; neither is required to arrive first.
   if(aw_pending&&w_pending&&!execute_pending&&!s_axi_bvalid) begin
    aw_pending<=0;w_pending<=0;execute_pending<=1;write_response<=0;
    enable_actions<=0;reset_actions<=0;commit_actions<=0;
    for(i=0;i<4;i=i+1) begin
     shadow_actions[i]<=0;
     if(write_channel==2'(i)) application_data[i]<=write_data[15:0];
    end
    if(write_address[11:10]!=0||write_address[1:0]!=0) write_response<=2;
    else case(write_offset)
     8'h00:begin
      if(|written_bits[31:2]) write_response<=2;
      else for(i=0;i<4;i=i+1) if(write_channel==2'(i)) begin
       enable_actions[i]<=write_strobe[0];
       reset_actions[i]<=written_bits[1];
      end
     end
     8'h04:begin
      if(|written_bits[31:12]) write_response<=2;
      else for(i=0;i<4;i=i+1) if(write_channel==2'(i)) begin
       shadow_actions[i][0+:2]<=write_strobe[1:0];
      end
     end
     8'h08:begin
      if(|written_bits[31:16]) write_response<=2;
      else for(i=0;i<4;i=i+1) if(write_channel==2'(i)) begin
       shadow_actions[i][2+:2]<=write_strobe[1:0];
      end
     end
     8'h0c:begin
      if(|written_bits[31:16]) write_response<=2;
      else for(i=0;i<4;i=i+1) if(write_channel==2'(i)) begin
       shadow_actions[i][4+:2]<=write_strobe[1:0];
      end
     end
     8'h10:begin
      if(|written_bits[31:16]) write_response<=2;
      else for(i=0;i<4;i=i+1) if(write_channel==2'(i)) begin
       shadow_actions[i][6+:2]<=write_strobe[1:0];
      end
     end
     8'h14:begin
      if(|written_bits[31:16]) write_response<=2;
      else for(i=0;i<4;i=i+1) if(write_channel==2'(i)) begin
       shadow_actions[i][8+:2]<=write_strobe[1:0];
      end
     end
     8'h18:begin
      if((|written_bits[31:1])||(written_bits[0]&&pending[write_channel])) write_response<=2;
      else for(i=0;i<4;i=i+1) if(write_channel==2'(i)) commit_actions[i]<=written_bits[0];
     end
     default:write_response<=2;
    endcase
   end
   if(s_axi_rvalid&&s_axi_rready) s_axi_rvalid<=0;
   if(s_axi_arvalid&&s_axi_arready) begin
    s_axi_rvalid<=1;s_axi_rresp<=0;s_axi_rdata<=0;
    if(s_axi_araddr==12'hffc) s_axi_rdata<=32'h41444153;
    else if(s_axi_araddr[11:10]!=0||s_axi_araddr[1:0]!=0) s_axi_rresp<=2;
    else case(read_offset)
     8'h00:s_axi_rdata<={31'b0,enabled[read_channel]};
     8'h04:s_axi_rdata<={16'b0,shadows[read_channel][0]};
     8'h08:s_axi_rdata<={16'b0,shadows[read_channel][1]};
     8'h0c:s_axi_rdata<={16'b0,shadows[read_channel][2]};
     8'h10:s_axi_rdata<={16'b0,shadows[read_channel][3]};
     8'h14:s_axi_rdata<={16'b0,shadows[read_channel][4]};
     8'h18:s_axi_rdata<={31'b0,pending[read_channel]};
     8'h1c:s_axi_rdata<={29'b0,pending[read_channel],channel_active[read_channel],enabled[read_channel]};
     8'h20:s_axi_rdata<=admitted[read_channel];
     8'h24:s_axi_rdata<=committed[read_channel];
     8'h28:s_axi_rdata<=aborted[read_channel];
     8'h2c:s_axi_rdata<=overflow[read_channel];
     8'h30:s_axi_rdata<=framing[read_channel];
     8'h34:s_axi_rdata<=accepted_inputs[read_channel*32+:32];
     8'h38:s_axi_rdata<=accepted_outputs[read_channel*32+:32];
     8'h3c:s_axi_rdata<=unadmitted[read_channel];
     8'h40:s_axi_rdata<={16'b0,fifo_levels[read_channel*16+:16]};
     8'h44:s_axi_rdata<={16'b0,fifo_peaks[read_channel*16+:16]};
     8'h48:s_axi_rdata<={16'b0,epochs[read_channel*16+:16]};
     8'h4c:s_axi_rdata<={16'b0,active_config_versions[read_channel*16+:16]};
     default:s_axi_rresp<=2;
    endcase
   end
  end
 end
endmodule
