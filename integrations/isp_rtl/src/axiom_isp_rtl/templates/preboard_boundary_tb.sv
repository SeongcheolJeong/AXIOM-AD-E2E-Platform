`timescale 1ns/1ps
module preboard_boundary_tb;
 parameter WORDS=256,SOURCE_PERIOD=14,SINK_PERIOD=10,HOLD=4,LOCK_DELAY=8,SEED=42;
 parameter BAD_RESET=0,BAD_CONSUMER=0;
 reg wc=0,rc=0,por_n=1,locked=1;
 wire wr,rr,wr_good,rr_good;
 reg valid=0,ready=0;reg [31:0] data=0;
 wire accept,rv;wire [31:0] result;
 integer sent=0,received=0,errors=0,fd,epoch=0,cycles=0;
 integer w_release_edges=0,r_release_edges=0;
 integer startup_unknown=0,reset_checks=0,consumer_checks=0;
 integer before_loss=0,pending_at_loss=0;
 reg [31:0] random_state=SEED;
 reg frame_committed=0,publish=0;
 axiom_reset_boundary a(wc,por_n,locked,wr_good),b(rc,por_n,locked,rr_good);
 assign wr=BAD_RESET?!(por_n&&locked):wr_good;
 assign rr=BAD_RESET?!(por_n&&locked):rr_good;
 axiom_boundary_fifo fifo(wc,wr,rc,rr,valid,accept,data,rv,ready,result);
 always #(SOURCE_PERIOD/2) wc=~wc;
 initial begin #1;forever #(SINK_PERIOD/2) rc=~rc;end
 always @(negedge wc) begin
  if(wr) begin valid=0;data=0;end
  else begin valid=(sent<WORDS);data=sent+epoch*65536;end
 end
 always @(negedge rc) begin
  random_state={random_state[30:0],random_state[31]^random_state[21]^random_state[1]^random_state[0]};
  ready=!rr&&random_state[1:0]!=0;
 end
 always @(posedge wc) begin
  if(wr) sent=0;else if(valid&&accept) sent=sent+1;
  if(por_n&&locked) begin
   if(w_release_edges<2&&wr!==1'b1) begin errors=errors+1;$display("BAD_RESET_DETECTED");end
   w_release_edges=w_release_edges+1;
  end else w_release_edges=0;
 end
 always @(posedge rc) begin
  cycles=cycles+1;
  if(rr) received=0;
  else if(rv&&ready) begin
   if(result!==(received+epoch*65536)) begin errors=errors+1;$display("DATA_ERROR %0d %h",received,result);end
   received=received+1;
  end
  if(por_n&&locked) begin
   if(r_release_edges<2&&rr!==1'b1) begin errors=errors+1;$display("BAD_RESET_DETECTED");end
   r_release_edges=r_release_edges+1;
  end else r_release_edges=0;
  $fdisplay(fd,"%0t,%0d,%0d,%0d,%0d,%0d,%0d,%0d",$time,epoch,wr,rr,sent,received,rv,ready);
 end
 initial begin
  fd=$fopen("native.csv","w");$fdisplay(fd,"time_ps,epoch,write_reset,read_reset,sent,received,valid,ready");
  $dumpfile("boundary.vcd");$dumpvars(0,preboard_boundary_tb);
  #0.5;if(wr_good===1'bx&&rr_good===1'bx) startup_unknown=1;
  #0.5;por_n=0;locked=0;
  #(SOURCE_PERIOD*HOLD);por_n=1;
  #(SOURCE_PERIOD*LOCK_DELAY+0.25);locked=1;
  #0.25;reset_checks=reset_checks+1;
  if(wr!==1'b1||rr!==1'b1) begin errors=errors+1;$display("BAD_RESET_DETECTED");end
  wait(sent>=WORDS/2);before_loss=received;pending_at_loss=sent-received;
  if(pending_at_loss<=0) begin errors=errors+1;$display("NO_INFLIGHT_RESET_EXERCISED");end
  // PLL loss invalidates queued words; epoch tags detect stale post-reset data.
  locked=0;#0.25;
  if(wr!==1'b1||rr!==1'b1) errors=errors+1;
  epoch=1;#(SOURCE_PERIOD*HOLD);
  locked=1;#0.25;reset_checks=reset_checks+1;
  if(wr!==1'b1||rr!==1'b1) begin errors=errors+1;$display("BAD_RESET_DETECTED");end
  wait(received==WORDS);#(SOURCE_PERIOD*4);
  // Negative-control oracle only: this is not the actual ISP frame consumer.
  frame_committed=0;publish=BAD_CONSUMER;#1;consumer_checks=consumer_checks+1;
  if(publish&&!frame_committed) begin errors=errors+1;$display("BAD_CONSUMER_DETECTED");end
  frame_committed=1;publish=1;#1;consumer_checks=consumer_checks+1;
  if(!startup_unknown) begin errors=errors+1;$display("FOUR_STATE_STARTUP_NOT_OBSERVED");end
  $display("PREBOARD_RESULT errors=%0d startup_unknown=%0d reset_checks=%0d consumer_checks=%0d words_after_relock=%0d received_before_loss=%0d pending_at_loss=%0d",errors,startup_unknown,reset_checks,consumer_checks,received,before_loss,pending_at_loss);
  $fclose(fd);if(errors) $fatal(1,"boundary checks failed");$finish;
 end
 initial begin #(2000000);$fatal(1,"boundary watchdog");end
endmodule
