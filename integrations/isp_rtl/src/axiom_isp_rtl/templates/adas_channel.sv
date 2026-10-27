// AXIOM streaming ISP channel. Camera ingress is not backpressurable.
// Terminal events cover admitted frames. A full event queue prevents admission.
module axiom_adas_channel #(
 parameter integer WIDTH=1920, HEIGHT=1080, CFA=3,
 parameter integer INIT_BLACK=64,
 parameter [63:0] INIT_GAINS=64'h0080008000800080
)(
 input wire clk, reset, enabled, local_reset, config_commit,
 input wire [15:0] shadow_black,
 input wire [63:0] shadow_gains,
 input wire cam_valid,cam_sof,cam_eol,
 input wire [15:0] cam_data,cam_frame_id,
 output wire out_valid, input wire out_ready,
 output wire [35:0] out_data,
 output wire out_sof,out_eol,
 output wire [15:0] out_frame_id,out_epoch,out_config_version,
 output wire event_valid,input wire event_ready,
 output wire event_kind,
 output wire [7:0] event_reason,
 output wire [15:0] event_frame_id,event_epoch,event_config_version,
 output wire [15:0] fifo_level,output reg [15:0] fifo_peak,
 output reg active,output reg pending,
 output reg [15:0] epoch,config_version,
 output reg [31:0] admitted_frames,committed_frames,aborted_frames,
 output reg [31:0] overflow_count,framing_errors,input_count,output_count,unadmitted_frames
);
 localparam integer OUT_WORDS=(WIDTH-4)*(HEIGHT-4);
 localparam integer MAX_RES=1 << $clog2(WIDTH);
 // 14-bit FIFO words: SOF, EOL, RAW12. Synchronous read infers block RAM.
 reg [13:0] fifo_ram [0:4095];
 reg [11:0] write_ptr,read_ptr;
 reg [12:0] ram_count;
 reg ram_nonempty;
 reg [12:0] stored_count;
 reg read_valid;
 reg [13:0] ram_output,read_hold_word;
 reg read_hold_valid;
 wire [13:0] read_word=read_hold_valid?read_hold_word:ram_output;
 reg isp_ingress_valid;
 reg [13:0] isp_raw_word,isp_hold_word;
 reg isp_hold_valid;
 wire [13:0] isp_ingress_word=isp_hold_valid?isp_hold_word:isp_raw_word;
 reg [$clog2(WIDTH)-1:0] input_x;
 reg [$clog2(HEIGHT)-1:0] input_y;
 reg [$clog2(WIDTH*HEIGHT+1)-1:0] input_words;
 reg [$clog2(OUT_WORDS+1)-1:0] frame_output_words;
 reg [15:0] frame_id,frame_epoch,frame_config;
 reg [15:0] active_black,pending_black;
 reg [63:0] active_gains,pending_gains;
 reg flush;

 reg egress_valid,egress_sof,egress_eol;
 reg [35:0] egress_data;
 reg [15:0] egress_frame,egress_epoch,egress_config;
 assign out_valid=egress_valid;
 assign out_data=egress_data;assign out_sof=egress_sof;assign out_eol=egress_eol;
 assign out_frame_id=egress_frame;assign out_epoch=egress_epoch;assign out_config_version=egress_config;

 reg [1:0] event_read,event_write;
 reg [2:0] event_count;
 reg [56:0] events [0:3];
 reg fault_pending,finish_pending;
 reg [7:0] fault_reason;
 assign event_valid=(event_count!=0);
 assign {event_kind,event_reason,event_frame_id,event_epoch,event_config_version}=events[event_read];
 wire event_pop=event_valid&&event_ready;
 wire admit=cam_valid&&cam_sof&&!active&&enabled&&!local_reset&&!flush&&!fault_pending&&!finish_pending&&(event_count<4);
 wire bad_start=cam_eol||(cam_data[15:12]!=0);
 wire malformed=cam_valid&&active&&(!cam_sof)&&
   ((cam_frame_id!=frame_id)||(cam_data[15:12]!=0)||(cam_eol!=(input_x==$bits(input_x)'(WIDTH-1)))||
    (input_words>=$bits(input_words)'(WIDTH*HEIGHT)));
 wire overlap=cam_valid&&cam_sof&&active;
 wire fifo_output_ready,blc_input_ready;
 wire fifo_output_valid,fifo_output_sof,fifo_output_eol;
 wire [15:0] fifo_output_data;
 // A one-beat ISP ingress stage cuts EBR clock-to-data before the skid mux.
 // FIFO occupancy ends when this stage accepts a beat; this stage and the
 // later ISP pipeline are separate from the 4096-beat RAM/prefetch capacity.
 wire isp_ingress_ready=!isp_ingress_valid||fifo_output_ready;
 wire consume=read_valid&&isp_ingress_ready&&!flush;
 // Explicit occupancy FFs avoid an adder followed by overflow/reset logic.
 assign fifo_level={3'b0,stored_count};
 wire full=stored_count[12]&&!consume;
 wire overflow=cam_valid&&active&&!cam_sof&&full;
 // Fault classification is a dedicated stage; it never drives the ready
 // chain, event RAM or active-state retirement in the same cycle.
 wire fault_detect=active&&!fault_pending&&!finish_pending&&
   (local_reset||!enabled||overlap||malformed||overflow);
 wire abort_now=active&&fault_pending;
 wire [7:0] abort_reason=local_reset?8'd2:!enabled?8'd4:overlap?8'd5:malformed?8'd3:8'd1;
 wire output_transfer=egress_valid&&out_ready;
 wire frame_transfer=output_transfer&&active&&!fault_pending&&!finish_pending&&
   (egress_frame==frame_id)&&(egress_epoch==frame_epoch);
 wire finish_detect=frame_transfer&&(frame_output_words==$bits(frame_output_words)'(OUT_WORDS-1));
 wire frame_complete=active&&finish_pending&&!fault_pending;
 wire event_push=active&&(fault_pending||finish_pending);
 wire fifo_push=(admit&&!bad_start)||(cam_valid&&active&&!fault_pending&&!finish_pending&&!full&&!flush);
 // Registered status keeps the wide count reduction off the EBR read-enable
 // path. It describes the current count, including simultaneous push/fetch.
 wire fifo_fetch=(!read_valid||consume)&&ram_nonempty&&!flush;
 wire core_valid,core_ready,core_sof,core_eol;
 wire [39:0] core_data;
 wire core_take=core_valid&&core_ready&&!flush&&!fault_pending&&!finish_pending&&active;
 // Do not feed combinational overflow/abort back into the ready chain.
 // Internal ready only describes egress capacity. During abort/retirement,
 // speculative internal beats may drain but core_take prevents publication;
 // registered flush invalidates them without a control-to-ready route.
 assign core_ready=!egress_valid||out_ready;
 // Faults retire one cycle after detection. Any beat already presented remains
 // stable until accepted; terminal abort invalidates the complete partial frame.
 // Registered flush clears speculative internal work after fault retirement.
 wire rstn=!(reset||flush);
 wire blc_valid,blc_ready,blc_sof,blc_eol,wb_valid,wb_ready,wb_sof,wb_eol;
 wire [15:0] blc_data,wb_data;
 wire gain_valid,gain_ready,gain_sof,gain_eol,mhc_valid,mhc_ready,mhc_sof,mhc_eol;
 wire [15:0] gain_data,mhc_data;
 wire demosaic_valid,demosaic_ready,demosaic_sof,demosaic_eol;
 wire [39:0] demosaic_data;
 // Isolate FIFO occupancy/overflow from the BLC multi-stage ready chain.
 skidbuffer #(.DATA_WIDTH(16)) after_fifo(
  .clk(clk),.rstn(rstn),.s_axis_tvalid(isp_ingress_valid),.s_axis_tready(fifo_output_ready),
  .s_axis_tdata({4'b0,isp_ingress_word[11:0]}),.s_axis_tuser(isp_ingress_word[13]),.s_axis_tlast(isp_ingress_word[12]),
  .m_axis_tvalid(fifo_output_valid),.m_axis_tready(blc_input_ready),.m_axis_tdata(fifo_output_data),
  .m_axis_tuser(fifo_output_sof),.m_axis_tlast(fifo_output_eol));
 // Configuration is frozen at admission, before the FIFO exposes its SOF.
 blc #(.PIXEL_PER_CYCLE(1),.PIXEL_BIT_WIDTH(12)) b(
  .clk(clk),.rstn(rstn),.black_level(active_black),
  .s_axis_tvalid(fifo_output_valid),.s_axis_tready(blc_input_ready),
  .s_axis_tdata(fifo_output_data),.s_axis_tuser(fifo_output_sof),.s_axis_tlast(fifo_output_eol),
  .m_axis_tvalid(blc_valid),.m_axis_tready(blc_ready),.m_axis_tdata(blc_data),.m_axis_tuser(blc_sof),.m_axis_tlast(blc_eol));
 // Registered-ready skid boundaries prevent a combinational ready path through
 // BLC, gain, MHC, overflow detection and FIFO state in a single clock cycle.
 skidbuffer #(.DATA_WIDTH(16)) after_blc(
  .clk(clk),.rstn(rstn),.s_axis_tvalid(blc_valid),.s_axis_tready(blc_ready),
  .s_axis_tdata(blc_data),.s_axis_tuser(blc_sof),.s_axis_tlast(blc_eol),
  .m_axis_tvalid(gain_valid),.m_axis_tready(gain_ready),.m_axis_tdata(gain_data),
  .m_axis_tuser(gain_sof),.m_axis_tlast(gain_eol));
 // Gr is the green on a red row. Upstream g0/g1 are CFA-relative.
 wire [15:0] g0=(CFA==3||CFA==2)?active_gains[31:16]:active_gains[47:32];
 wire [15:0] g1=(CFA==3||CFA==2)?active_gains[47:32]:active_gains[31:16];
 axiom_adas_colorgain #(.CFA_ORIENTATION(CFA),.PIXEL_PER_CYCLE(1),.PIXEL_BIT_WIDTH(12)) g(
  .clk(clk),.rstn(rstn),.rgain(active_gains[15:0]),.g0gain(g0),.g1gain(g1),.bgain(active_gains[63:48]),
  .s_axis_tvalid(gain_valid),.s_axis_tready(gain_ready),.s_axis_tdata(gain_data),.s_axis_tuser(gain_sof),.s_axis_tlast(gain_eol),
  .m_axis_tvalid(wb_valid),.m_axis_tready(wb_ready),.m_axis_tdata(wb_data),.m_axis_tuser(wb_sof),.m_axis_tlast(wb_eol));
 skidbuffer #(.DATA_WIDTH(16)) after_gain(
  .clk(clk),.rstn(rstn),.s_axis_tvalid(wb_valid),.s_axis_tready(wb_ready),
  .s_axis_tdata(wb_data),.s_axis_tuser(wb_sof),.s_axis_tlast(wb_eol),
  .m_axis_tvalid(mhc_valid),.m_axis_tready(mhc_ready),.m_axis_tdata(mhc_data),
  .m_axis_tuser(mhc_sof),.m_axis_tlast(mhc_eol));
 axiom_adas_demosaic #(.CFA_ORIENTATION(CFA),.PIXEL_PER_CYCLE(1),.PIXEL_BIT_WIDTH(12),.MAX_RESOLUTION(MAX_RES)) d(
  .clk(clk),.rstn(rstn),.s_axis_tvalid(mhc_valid),.s_axis_tready(mhc_ready),
  .s_axis_tdata(mhc_data),.s_axis_tuser(mhc_sof),.s_axis_tlast(mhc_eol),
  .m_axis_tvalid(demosaic_valid),.m_axis_tready(demosaic_ready),.m_axis_tdata(demosaic_data),.m_axis_tuser(demosaic_sof),.m_axis_tlast(demosaic_eol));
 skidbuffer #(.DATA_WIDTH(40)) after_demosaic(
  .clk(clk),.rstn(rstn),.s_axis_tvalid(demosaic_valid),.s_axis_tready(demosaic_ready),
  .s_axis_tdata(demosaic_data),.s_axis_tuser(demosaic_sof),.s_axis_tlast(demosaic_eol),
  .m_axis_tvalid(core_valid),.m_axis_tready(core_ready),.m_axis_tdata(core_data),
  .m_axis_tuser(core_sof),.m_axis_tlast(core_eol));

 always @(posedge clk) begin
  if(fifo_push&&!reset) fifo_ram[write_ptr]<={cam_sof,cam_eol,cam_data[11:0]};
  // Poll the next unread address without a ready-to-EBR clock-enable path.
  // The first stalled cycle saves the already valid word before this read
  // changes; it remains the same single prefetched beat, not extra capacity.
  ram_output<=fifo_ram[read_ptr];
  // Capture EBR data without a mux or enable; select held data afterwards.
  isp_raw_word<=ram_output;
 end
 always @(posedge clk) begin
  if(reset) begin
   active<=0;pending<=0;flush<=0;epoch<=0;config_version<=0;
   active_black<=16'(INIT_BLACK);pending_black<=16'(INIT_BLACK);active_gains<=INIT_GAINS;pending_gains<=INIT_GAINS;
   write_ptr<=0;read_ptr<=0;ram_count<=0;ram_nonempty<=0;stored_count<=0;read_valid<=0;read_hold_valid<=0;fifo_peak<=0;
   isp_ingress_valid<=0;isp_hold_word<=0;isp_hold_valid<=1;
   input_x<=0;input_y<=0;input_words<=0;frame_output_words<=0;
   frame_id<=0;frame_epoch<=0;frame_config<=0;
   egress_valid<=0;egress_sof<=0;egress_eol<=0;egress_data<=0;egress_frame<=0;egress_epoch<=0;egress_config<=0;
   event_read<=0;event_write<=0;event_count<=0;fault_pending<=0;finish_pending<=0;fault_reason<=0;
   admitted_frames<=0;committed_frames<=0;aborted_frames<=0;overflow_count<=0;
   framing_errors<=0;input_count<=0;output_count<=0;unadmitted_frames<=0;
  end else begin
   flush<=0;
   if(config_commit) begin pending_black<=shadow_black;pending_gains<=shadow_gains;pending<=1;end
   fault_pending<=fault_detect;
   finish_pending<=finish_detect;
   // Only fault_pending makes this value observable at retirement; sampling
   // every cycle removes the full/consume path from the reason FF enables.
   fault_reason<=abort_reason;
   if(event_pop) event_read<=event_read+1'b1;
   if(event_push) begin
    events[event_write]<={!fault_pending,fault_pending?fault_reason:8'd0,frame_id,frame_epoch,frame_config};
    event_write<=event_write+1'b1;
   end
   case({event_push,event_pop})
    2'b10:event_count<=event_count+1'b1;2'b01:event_count<=event_count-1'b1;default:;
   endcase
   // Egress owns its already presented beat across a channel-local reset/abort.
   if(output_transfer) begin egress_valid<=0;output_count<=output_count+1'b1;end
   if(core_take) begin
    egress_valid<=1;egress_data<={core_data[35:24],core_data[11:0],core_data[23:12]};
    egress_sof<=core_sof;egress_eol<=core_eol;
    egress_frame<=frame_id;egress_epoch<=frame_epoch;egress_config<=frame_config;
   end
   if(isp_ingress_ready) begin
    isp_ingress_valid<=read_valid&&!flush;
    isp_hold_valid<=read_hold_valid;
    if(read_hold_valid) isp_hold_word<=read_hold_word;
   end else if(!isp_hold_valid) begin
    // Preserve the current raw-register beat when the ISP stops accepting.
    isp_hold_word<=isp_raw_word;isp_hold_valid<=1;
   end
   if(flush) isp_ingress_valid<=0;
   if(fifo_level>fifo_peak) fifo_peak<=fifo_level;
   if(fifo_fetch) begin read_ptr<=read_ptr+1'b1;read_valid<=1;end
   else if(consume) read_valid<=0;
   if(read_valid&&!consume&&!read_hold_valid) begin read_hold_word<=ram_output;read_hold_valid<=1;end
   if(consume) read_hold_valid<=0;
   if(fifo_push) write_ptr<=write_ptr+1'b1;
   if(fifo_push&&!abort_now) input_count<=input_count+1'b1;
   case({fifo_push,fifo_fetch})
    2'b10:begin ram_count<=ram_count+1'b1;ram_nonempty<=1;end
    2'b01:begin ram_count<=ram_count-1'b1;ram_nonempty<=(ram_count!=13'd1);end
    default:;
   endcase
   case({fifo_push,consume})
    2'b10:stored_count<=stored_count+1'b1;2'b01:stored_count<=stored_count-1'b1;default:;
   endcase
   if(cam_valid&&cam_sof&&!admit) unadmitted_frames<=unadmitted_frames+1'b1;
   if(admit) begin
    if(bad_start) begin unadmitted_frames<=unadmitted_frames+1'b1;framing_errors<=framing_errors+1'b1;end
    else begin
     active<=1;frame_id<=cam_frame_id;frame_epoch<=epoch;
     input_x<=1;input_y<=0;input_words<=1;frame_output_words<=0;
     admitted_frames<=admitted_frames+1'b1;
     frame_config<=config_version+(pending?16'd1:16'd0);
     if(pending) begin active_black<=pending_black;active_gains<=pending_gains;config_version<=config_version+1'b1;pending<=0;end
    end
   end else if(fifo_push) begin
    input_words<=input_words+1'b1;
    if(input_x==$bits(input_x)'(WIDTH-1)) begin input_x<=0;input_y<=input_y+1'b1;end
    else input_x<=input_x+1'b1;
   end
   if(frame_transfer) frame_output_words<=frame_output_words+1'b1;
   if(frame_complete) begin active<=0;committed_frames<=committed_frames+1'b1;end
   if(abort_now) begin
    active<=0;flush<=1;epoch<=epoch+16'd1+(local_reset?16'd1:16'd0);aborted_frames<=aborted_frames+1'b1;
    if(fault_reason==8'd1) overflow_count<=overflow_count+1'b1;
    if(fault_reason==8'd3||fault_reason==8'd5) framing_errors<=framing_errors+1'b1;
   end else if(local_reset&&(!active||frame_complete)) begin
    flush<=1;epoch<=epoch+1'b1;
   end
   if(flush) begin
    write_ptr<=0;read_ptr<=0;ram_count<=0;ram_nonempty<=0;stored_count<=0;read_valid<=0;read_hold_valid<=0;
   end
  end
 end
endmodule
