#include "Vaxiom_isp_top.h"
#include "verilated.h"
#include <fstream>
#include <vector>
#include <memory>
#include <cstdint>
#include <algorithm>
#include <array>
#include <string>
// Only observable top-level ports are traced; no frame RAM array dump.
// Values come from the actual compiled RTL at both clock phases.
struct PortVcd {
 std::ofstream file; bool initial=true;
 std::array<uint64_t,23> previous{};
 const std::array<unsigned,23> widths={1,1,1,1,16,1,1,1,1,36,1,1,12,12,1,1,1,1,32,2,12,12,12};
 const std::array<const char*,23> names={"clk","reset","in_valid","in_ready","in_data","in_sof","in_eol","out_valid","out_ready","out_data","out_sof","out_eol","blc_data","wb_data","blc_valid","blc_ready","wb_valid","wb_ready","buffer_level","buffer_state","out_r","out_g","out_b"};
 PortVcd(const char* path):file(path) {
  file<<"$version AXIOM actual RTL top-port trace $end\n$timescale 1ns $end\n$scope module axiom_isp_top $end\n";
  for(unsigned i=0;i<names.size();++i)file<<"$var wire "<<widths[i]<<" v"<<i<<' '<<names[i]<<" $end\n";
  file<<"$upscope $end\n$enddefinitions $end\n";
 }
 void dump(uint64_t time,Vaxiom_isp_top& t) {
  std::array<uint64_t,23> values={t.clk,t.reset,t.in_valid,t.in_ready,t.in_data,t.in_sof,t.in_eol,t.out_valid,t.out_ready,t.out_data,t.out_sof,t.out_eol,t.blc_data,t.wb_data,t.blc_valid,t.blc_ready,t.wb_valid,t.wb_ready,t.buffer_level,t.buffer_state,(t.out_data>>24)&4095,(t.out_data>>12)&4095,t.out_data&4095};
  file<<'#'<<time<<'\n';
  for(unsigned i=0;i<values.size();++i)if(initial||values[i]!=previous[i]) {
   if(widths[i]==1)file<<values[i]<<'v'<<i<<'\n';
   else {file<<'b';for(int bit=widths[i]-1;bit>=0;--bit)file<<((values[i]>>bit)&1);file<<" v"<<i<<'\n';}
  }
  previous=values;initial=false;
 }
};
int main(int argc, char **argv) {
 if(argc!=6) return 2;
 std::ifstream raw(argv[1],std::ios::binary);
 std::vector<uint16_t> input; char pair[2];
 while(raw.read(pair,2)) input.push_back(uint16_t(uint8_t(pair[0]))|(uint16_t(uint8_t(pair[1]))<<8));
 if(input.size()!=@WORDS@u) return 3;
 std::ofstream trace(argv[2]),pixels(argv[3]),stats(argv[4]);
 auto ctx=std::make_unique<VerilatedContext>();ctx->commandArgs(argc,argv);
 auto top=std::make_unique<Vaxiom_isp_top>(ctx.get());
 PortVcd vcd(argv[5]);
 trace << "cycle,time,reset,in_valid,in_ready,in_data,in_sof,in_eol,out_valid,out_ready,out_r,out_g,out_b,out_sof,out_eol,blc_data,wb_data,buffer_level,buffer_state,accepted_in,accepted_out\n";
 pixels << "cycle,time,frame,index,r,g,b,sof,eol,epoch\n";
 uint32_t sent=0,completed=0,frame_words=0,epoch=0,received=0,discarded=0,accepted=0;
 uint32_t protocols=0,first_protocol_cycle=0,used_cycles=0,tail=0;
 bool held=false,source_held=false,output_started=false; uint64_t helddata=0; uint32_t heldsof=0,heldeol=0;
 int previous_buffer_state=-1;
 for(uint32_t cycle=0;cycle<@MAX_CYCLES@u;++cycle) {
  bool reset=cycle<2 || (cycle==@RESET@u), ready=!(@STALLS@);
  if(reset) { sent=completed*@FRAME_WORDS@u;discarded+=frame_words;frame_words=0;held=false;source_held=false;output_started=false;if(cycle==@RESET@u)++epoch; }
  // Deterministic source idle exercises ready/valid independently of output stalls.
  bool valid=!reset && sent<input.size() && (cycle%17!=5);
  // A source may not withdraw valid while stalled.
  if(!reset && source_held) valid=true;
  ctx->time(cycle*10ULL);top->clk=0;top->reset=reset;
  top->in_valid=valid;top->in_data=sent<input.size()?input[sent]:0;
  top->in_sof=(sent%@FRAME_WORDS@u)==0;top->in_eol=(sent%@WIDTH@u)==@WIDTH@u-1;
  top->out_ready=ready;top->eval();
  vcd.dump(ctx->time(),*top);
  uint32_t ir=top->in_ready,ov=top->out_valid,os=top->out_sof,oe=top->out_eol;
  uint64_t od=top->out_data;
  uint32_t ai=!reset&&valid&&ir,ao=!reset&&ov&&ready;
  uint32_t r=(od>>24)&4095,g=(od>>12)&4095,b=od&4095;
  if(!reset) {
   // The frame barrier must finish capturing the current frame before emitting
   // RGB, and must not accept any next-frame pixel until RGB fully drains.
   if((ov && sent<(completed+1)*@FRAME_WORDS@u) ||
      (ai && sent>=(completed+1)*@FRAME_WORDS@u)) {++protocols;if(!first_protocol_cycle)first_protocol_cycle=cycle;}
   if(held && (!ov||od!=helddata||os!=heldsof||oe!=heldeol)) {++protocols;if(!first_protocol_cycle)first_protocol_cycle=cycle;}
   held=ov&&!ready;helddata=od;heldsof=os;heldeol=oe;source_held=valid&&!ir;
   if(ao) {
    if(os!=(frame_words==0)||oe!=((frame_words%@OUT_WIDTH@u)==@OUT_WIDTH@u-1)||completed>=@FRAMES@u) {++protocols;if(!first_protocol_cycle)first_protocol_cycle=cycle;}
    pixels<<cycle<<','<<cycle*10ULL+5<<','<<completed<<','<<frame_words<<','<<r<<','<<g<<','<<b<<','<<os<<','<<oe<<','<<epoch<<'\n';
    ++received; if(++frame_words==@OUT_WORDS@u){++completed;frame_words=0;}
   }
   sent+=ai;accepted+=ai;
  }
  if(cycle<4096 || cycle%@STRIDE@u==0 || reset || (ov&&!output_started) || int(top->buffer_state)!=previous_buffer_state) {
   trace<<cycle<<','<<cycle*10ULL+5<<','<<reset<<','<<valid<<','<<ir<<','<<uint32_t(top->in_data)<<','<<uint32_t(top->in_sof)<<','<<uint32_t(top->in_eol)<<','<<ov<<','<<ready<<','<<r<<','<<g<<','<<b<<','<<os<<','<<oe<<','<<uint32_t(top->blc_data)<<','<<uint32_t(top->wb_data)<<','<<uint32_t(top->buffer_level)<<','<<uint32_t(top->buffer_state)<<','<<ai<<','<<ao<<'\n';
  }
  if(ov) output_started=true;
  previous_buffer_state=top->buffer_state;
  ctx->time(cycle*10ULL+5);top->clk=1;top->eval();vcd.dump(ctx->time(),*top);
  used_cycles=cycle+1;
  if(ctx->gotFinish())return 4;
  // Keep observing the tail. A later requested reset must still be executed.
  if(completed>=@FRAMES@u && (cycle>@RESET@u || @RESET@u==4294967295u)) {if(++tail==32)break;}
 }
 stats << "{\"completed_frames\":"<<completed<<",\"received_pixels\":"<<received<<",\"discarded_output_pixels\":"<<discarded<<",\"input_transfers\":"<<accepted<<",\"protocol_error_count\":"<<protocols<<",\"first_protocol_cycle\":"<<first_protocol_cycle<<",\"cycles\":"<<used_cycles<<",\"reset_count\":"<<epoch<<"}";
 top->final();return trace.good()&&pixels.good()&&stats.good()&&vcd.file.good()?0:5;
}
