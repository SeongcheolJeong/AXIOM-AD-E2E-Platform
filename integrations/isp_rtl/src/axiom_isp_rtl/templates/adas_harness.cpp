#include "Vaxiom_adas_top.h"
#include "verilated.h"
#include <array>
#include <vector>
#include <deque>
#include <string>
#include <fstream>
#include <sstream>
#include <iomanip>
#include <memory>
#include <algorithm>
#include <cstdint>
#include <filesystem>
#include <iostream>

// Portable SHA-256 over actual little-endian pixel bytes.
struct Sha256 {
 std::array<uint32_t,8> h={0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
 std::array<uint8_t,64> buf{}; uint64_t count=0; unsigned used=0;
 static uint32_t rr(uint32_t x,unsigned n){return (x>>n)|(x<<(32-n));}
 void block(){
  static const uint32_t k[64]={0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
  uint32_t w[64];for(unsigned i=0;i<16;i++)w[i]=(uint32_t(buf[i*4])<<24)|(uint32_t(buf[i*4+1])<<16)|(uint32_t(buf[i*4+2])<<8)|buf[i*4+3];
  for(unsigned i=16;i<64;i++){uint32_t a=w[i-15],b=w[i-2];w[i]=w[i-16]+(rr(a,7)^rr(a,18)^(a>>3))+w[i-7]+(rr(b,17)^rr(b,19)^(b>>10));}
  uint32_t a=h[0],b=h[1],c=h[2],d=h[3],e=h[4],f=h[5],g=h[6],z=h[7];
  for(unsigned i=0;i<64;i++){uint32_t t1=z+(rr(e,6)^rr(e,11)^rr(e,25))+((e&f)^((~e)&g))+k[i]+w[i];uint32_t t2=(rr(a,2)^rr(a,13)^rr(a,22))+((a&b)^(a&c)^(b&c));z=g;g=f;f=e;e=d+t1;d=c;c=b;b=a;a=t1+t2;}
  h[0]+=a;h[1]+=b;h[2]+=c;h[3]+=d;h[4]+=e;h[5]+=f;h[6]+=g;h[7]+=z;
 }
 void byte(uint8_t b){buf[used++]=b;++count;if(used==64){block();used=0;}}
 void word(uint16_t v){byte(v&255);byte(v>>8);}
 std::string hex() const {Sha256 s=*this;uint64_t bits=s.count*8;s.byte(128);while(s.used!=56)s.byte(0);for(int i=7;i>=0;--i)s.byte((bits>>(i*8))&255);std::ostringstream out;out<<std::hex<<std::setfill('0');for(auto v:s.h)out<<std::setw(8)<<v;return out.str();}
};

constexpr unsigned W=@WIDTH@,H=@HEIGHT@,OW=W-4,OH=H-4,N=@FRAMES@;
constexpr uint64_t LIMIT=@MAX_CYCLES@ULL,RATE=@PIXEL_RATE@ULL,RW=@RASTER_WIDTH@ULL,RH=@RASTER_HEIGHT@ULL,ORIGIN=256;
const unsigned cfa[4]={@CFA@},pattern[4]={@PATTERN@};
const unsigned site[4][4]={{0,1,2,3},{3,2,1,0},{1,0,3,2},{2,3,0,1}};
struct Settings{unsigned black;std::array<unsigned,4> gain;};
const unsigned initialBlack[4]={@BLACK@};
const std::array<unsigned,4> initialGain[4]={@GAINS@};
struct Fault{unsigned ch,kind;uint64_t at,duration;};
const std::vector<Fault> faults={@FAULTS@};
struct Update{unsigned ch;uint64_t at;int black;std::array<unsigned,4> gain;bool setGain;};
std::vector<Update> updates={@UPDATES@};
const std::vector<std::array<uint64_t,2>> windows={@WINDOWS@};
const bool rawRepeat[4]={@RAW_REPEAT@};
const std::vector<unsigned> previewFrames={@PREVIEW_FRAMES@};
struct FaultEdge {uint64_t at;unsigned ch,kind;int delta;};
uint64_t wide36(const VlWide<5>& p,unsigned ch){unsigned bit=ch*36,i=bit/32,s=bit%32;return ((uint64_t(p[i])>>s)|(uint64_t(p[i+1])<<(32-s)))&0xfffffffffULL;}
unsigned lane16(uint64_t p,unsigned ch){return (p>>(ch*16))&65535;}
unsigned clip(int v){return unsigned(std::max(0,std::min(4095,v/16)));}

struct Frame {
 Sha256 inputHash,outputHash,referenceHash;bool referenceDiverged=false;
 uint64_t firstInput=0,firstOutput=0,lastOutput=0,terminalTime=0;
 uint32_t received=0,mismatches=0,sourcePixels=0,fifoPeak=0;
 unsigned version=0,epoch=0,reason=0;int terminal=-1;bool settingsKnown=false, admitted=false, sourceStarted=false;
 Settings settings{};
 std::vector<uint8_t> rawPreview,rgbPreview;
 void allocatePreview(){rawPreview.resize(std::min(W,320u)*std::min(H,180u)*3);rgbPreview.resize(std::min(OW,320u)*std::min(OH,180u)*3);}
};

// At most two decoded RAW frames per channel: camera source and delayed oracle.
// No implicit source repetition. A one-frame file requires the explicit policy.
struct Inputs {
 struct Cache {int frame=-1;std::vector<uint16_t> data;};
 std::array<std::ifstream,4> files;
 std::array<std::array<Cache,2>,4> cache;
 std::array<unsigned,4> hot{};
 uint64_t loads=0;
 Inputs(char **argv){for(unsigned ch=0;ch<4;ch++)if(std::string(argv[ch])!="-"){
  files[ch].open(argv[ch],std::ios::binary|std::ios::ate);
  if(!files[ch]||uint64_t(files[ch].tellg())!=uint64_t(W)*H*(rawRepeat[ch]?1:N)*2)throw std::runtime_error("Wrong RAW input size or missing explicit repeat policy");
 }}
 const std::vector<uint16_t>& raw(unsigned ch,unsigned frame){
  unsigned source=rawRepeat[ch]?0:frame;
  if(cache[ch][hot[ch]].frame==int(source))return cache[ch][hot[ch]].data;
  unsigned next=1-hot[ch];auto& entry=cache[ch][next];hot[ch]=next;
  if(entry.frame!=int(source)){
   entry.data.resize(uint64_t(W)*H);auto& file=files[ch];file.clear();file.seekg(uint64_t(source)*W*H*2);
   file.read(reinterpret_cast<char*>(entry.data.data()),entry.data.size()*2);
   if(!file)throw std::runtime_error("RAW frame read failed");
   for(auto v:entry.data)if(v>4095)throw std::runtime_error("RAW12 out of range");
   entry.frame=source;++loads;
  }
  return entry.data;
 }
 uint64_t cacheBytes()const{uint64_t bytes=0;for(auto& channel:cache)for(auto& entry:channel)bytes+=entry.data.size()*2;return bytes;}
 unsigned pixel(unsigned ch,unsigned frame,unsigned x,unsigned y) {
  if(files[ch].is_open())return raw(ch,frame)[uint64_t(y)*W+x];
  if(pattern[ch]==0)return (x*37+y*61+(@SEED@u+ch*101)%4096+frame*257)%4096;
  if(pattern[ch]==2)return ((x>=W/2-1&&x<W/2+1&&y>=H/2-1&&y<H/2+1)?4095:(x==2&&y==2?(@SEED@u+ch*101)%4096:0));
  const unsigned colors[8][3]={{4095,4095,4095},{4095,4095,0},{0,4095,4095},{0,4095,0},{4095,0,4095},{4095,0,0},{0,0,4095},{0,0,0}};
  unsigned s=site[cfa[ch]][(y%2)*2+x%2],component=s==0?0:s==3?2:1;return colors[std::min(x*8/W,7u)][component];
 }
};

// BEGIN TRANSACTIONAL CONSUMER -- independent from the spatial pixel oracle.
// The receiver stages a digest/count, publishing it only after a valid commit.
// Abort may precede one already-held egress beat; that beat remains discarded.
struct Consumer {
 Sha256 stagedHash;uint64_t pixels=0,discarded=0,lateDiscarded=0,publishedAt=0;
 unsigned epoch=0,version=0;int terminal=-1;
 bool identityKnown=false,poisoned=false,visible=false,abortedVisible=false;
 const char* pixel(uint64_t value,unsigned frameEpoch,unsigned frameVersion,bool sof,bool eol){
  if(terminal==0){++pixels;++discarded;++lateDiscarded;return nullptr;}
  if(terminal==1){++pixels;poisoned=true;return "consumer_pixel_after_commit";}
  const char* error=nullptr;
  if(!identityKnown){epoch=frameEpoch;version=frameVersion;identityKnown=true;}
  if(epoch!=frameEpoch||version!=frameVersion)error="consumer_mixed_identity";
  if(pixels>=uint64_t(OW)*OH||sof!=(pixels==0)||eol!=(pixels%OW==OW-1))error="consumer_pixel_framing";
  for(unsigned shift:{24u,12u,0u})stagedHash.word((value>>shift)&4095);
  ++pixels;if(error)poisoned=true;return error;
 }
 const char* finish(bool commit,unsigned frameEpoch,unsigned frameVersion,uint64_t time){
  if(!commit&&visible)abortedVisible=true;
  if(terminal>=0){poisoned=true;return "consumer_duplicate_terminal";}
  terminal=commit?1:0;
  const char* error=nullptr;
  if(identityKnown&&(epoch!=frameEpoch||version!=frameVersion))error="consumer_terminal_identity";
  epoch=frameEpoch;version=frameVersion;identityKnown=true;
  if(!commit){discarded=pixels;if(error)poisoned=true;return error;}
  if(pixels!=uint64_t(OW)*OH)error="consumer_incomplete_commit";
  if(poisoned&&!error)error="consumer_poisoned_commit";
  if(error){poisoned=true;return error;}
  visible=true;publishedAt=time;return nullptr;
 }
};
// END TRANSACTIONAL CONSUMER

// Spatial oracle: no RTL state, line-memory implementation, or scheduling reused.
struct Oracle {
 int frame=-1;unsigned channel=0;Settings config{};std::vector<uint16_t> wb;
 void load(Inputs& input,unsigned ch,unsigned f,const Settings& s){
  if(frame==int(f)&&channel==ch&&config.black==s.black&&config.gain==s.gain)return;
  frame=int(f);channel=ch;config=s;wb.resize(W*H);
  for(unsigned y=0;y<H;y++)for(unsigned x=0;x<W;x++){
   int v=int(input.pixel(ch,f,x,y))-int(s.black);unsigned color=site[cfa[ch]][(y%2)*2+x%2];
   wb[y*W+x]=std::min(4095u,(unsigned(std::max(v,0))*s.gain[color])/128);
  }
 }
 std::array<unsigned,3> pixel(unsigned x,unsigned y) const {
  unsigned p=(y+2)*W+x+2;int c=wb[p],lr=wb[p-1]+wb[p+1],ud=wb[p-W]+wb[p+W],lr2=wb[p-2]+wb[p+2],ud2=wb[p-2*W]+wb[p+2*W],diag=wb[p-W-1]+wb[p-W+1]+wb[p+W-1]+wb[p+W+1];
  unsigned green=clip(8*c+4*(lr+ud)-2*(lr2+ud2));
  unsigned horizontal=clip(10*c+8*lr-2*(lr2+diag)+ud2),vertical=clip(10*c+8*ud-2*(ud2+diag)+lr2);
  unsigned cross=clip(12*c+4*diag-3*(lr2+ud2));
  unsigned color=site[cfa[channel]][(y%2)*2+x%2];
  if(color==0)return {unsigned(c),green,cross};if(color==3)return {cross,green,unsigned(c)};
  if(color==1)return {horizontal,unsigned(c),vertical};return {vertical,unsigned(c),horizontal};
 }
};

void preview(std::vector<uint8_t>& target,unsigned w,unsigned h,unsigned x,unsigned y,const std::array<unsigned,3>& values){
 if(target.empty())return;
 unsigned pw=std::min(w,320u),ph=std::min(h,180u),px=x*pw/w,py=y*ph/h;
 for(unsigned j=0;j<3;j++)target[(py*pw+px)*3+j]=values[j]>>4;
}
void ppm(const std::string& path,unsigned w,unsigned h,const std::vector<uint8_t>& pixels){std::ofstream out(path,std::ios::binary);out<<"P6\n"<<std::min(w,320u)<<' '<<std::min(h,180u)<<"\n255\n";out.write(reinterpret_cast<const char*>(pixels.data()),pixels.size());}

struct Write{uint64_t at;unsigned addr,data;};
struct Bus {
 std::deque<Write> queue;bool aw=false,wd=false,waiting=false;Write current{};
 void add(uint64_t at,unsigned addr,unsigned data){queue.push_back({at,addr,data});}
 void drive(Vaxiom_adas_top& t,uint64_t cycle){
  if(!waiting&&!queue.empty()&&queue.front().at<=cycle){current=queue.front();queue.pop_front();waiting=true;aw=wd=false;}
  t.s_axi_awvalid=waiting&&!aw;t.s_axi_awaddr=current.addr;t.s_axi_wvalid=waiting&&!wd;t.s_axi_wdata=current.data;t.s_axi_wstrb=15;t.s_axi_bready=1;
  t.s_axi_arvalid=0;t.s_axi_araddr=0;t.s_axi_rready=1;
 }
 bool sample(Vaxiom_adas_top& t,Write& applied){
  bool done=false;if(waiting){if(t.s_axi_awvalid&&t.s_axi_awready)aw=true;if(t.s_axi_wvalid&&t.s_axi_wready)wd=true;if(t.s_axi_bvalid){applied=current;done=true;waiting=false;}}return done;
 }
};

struct Vcd {
 std::ofstream out;std::array<uint64_t,29> prev{};bool first=true;
 Vcd(const std::string& path):out(path){out<<"$version AXIOM bounded native top ports $end\n$timescale 1ns $end\n$scope module adas $end\n$var wire 1 v0 clk $end\n";for(unsigned ch=0;ch<4;ch++){const char* names[]={"cam_valid","out_valid","out_ready","fifo_level","event_valid","event_kind","config_version"};for(unsigned j=0;j<7;j++)out<<"$var wire "<<(j==3||j==6?16:1)<<" v"<<1+ch*7+j<<" ch"<<ch<<'_'<<names[j]<<" $end\n";}out<<"$upscope $end\n$enddefinitions $end\n";}
 void dump(uint64_t time,Vaxiom_adas_top& t){std::array<uint64_t,29> v{};v[0]=t.clk;for(unsigned ch=0;ch<4;ch++){unsigned i=1+ch*7;v[i]=(t.cam_valid>>ch)&1;v[i+1]=(t.out_valid>>ch)&1;v[i+2]=(t.out_ready>>ch)&1;v[i+3]=lane16(t.fifo_levels,ch);v[i+4]=(t.event_valid>>ch)&1;v[i+5]=(t.event_kind>>ch)&1;v[i+6]=lane16(t.active_config_versions,ch);}out<<'#'<<time<<'\n';for(unsigned i=0;i<v.size();i++)if(first||v[i]!=prev[i]){out<<'b';for(int b=(i>0&&((i-1)%7==3||(i-1)%7==6)?15:0);b>=0;b--)out<<((v[i]>>b)&1);out<<" v"<<i<<'\n';}prev=v;first=false;}
};

int main(int argc,char **argv){try{
 if(argc!=6)return 2;
 std::string directory=argv[1];std::filesystem::create_directories(directory+"/previews");Inputs input(argv+2);
 auto context=std::make_unique<VerilatedContext>();context->commandArgs(argc,argv);context->randReset(@INIT_RESET@);context->randSeed(@INIT_SEED@);auto top=std::make_unique<Vaxiom_adas_top>(context.get());
 std::array<std::vector<Frame>,4> frames;for(auto& v:frames){v.resize(N);for(auto id:previewFrames)v.at(id).allocatePreview();}
 std::array<std::vector<Consumer>,4> consumers;for(auto& v:consumers)v.resize(N);
 uint64_t previewBytes=0;for(auto& channel:frames)for(auto& f:channel)previewBytes+=f.rawPreview.size()+f.rgbPreview.size();
 std::stable_sort(updates.begin(),updates.end(),[](const Update&a,const Update&b){return a.at<b.at;});
 std::vector<FaultEdge> edges;for(auto f:faults){edges.push_back({f.at,f.ch,f.kind,1});if(f.kind<2)edges.push_back({f.at+f.duration,f.ch,f.kind,-1});}
 std::stable_sort(edges.begin(),edges.end(),[](const FaultEdge&a,const FaultEdge&b){return a.at<b.at;});
 size_t updateCursor=0,faultCursor=0,windowCursor=0;std::array<std::array<int,2>,4> faultActive{};
 std::array<Settings,4> shadow,active,pending;std::array<bool,4> hasPending{};std::array<unsigned,4> versions{};
 for(unsigned ch=0;ch<4;ch++)shadow[ch]=active[ch]=pending[ch]={initialBlack[ch],initialGain[ch]};
 Bus bus;for(unsigned ch=0;ch<4;ch++)bus.add(4,ch*256,1);
 std::array<Oracle,4> oracles;std::array<bool,4> held{};std::array<uint64_t,4> heldData{};std::array<unsigned,4> heldSof{},heldEol{},heldFrame{},heldEpoch{},heldVersion{};
 std::ofstream trace(directory+"/native.csv"),errors(directory+"/pixel-errors.jsonl"),events(directory+"/events.jsonl");Vcd vcd(directory+"/waveform.vcd");
 trace<<"cycle,time";for(unsigned ch=0;ch<4;ch++)for(const char* name:{"cam_valid","out_valid","out_ready","fifo_level","event_valid","event_kind","config_version"})trace<<",ch"<<ch<<'_'<<name;trace<<'\n';
 uint64_t phase=0,ticks=0,cycles=0,sourceEnd=0,protocols=0,mismatches=0,tail=0,consumerProtocols=0;unsigned errorRows=0;
 auto protocol=[&](uint64_t cycle,unsigned ch,const char* why){++protocols;if(errorRows++<100)errors<<"{\"kind\":\"protocol\",\"cycle\":"<<cycle<<",\"channel\":"<<ch<<",\"reason\":\""<<why<<"\"}\n";};
 auto consumerError=[&](uint64_t cycle,unsigned ch,const char* why){if(why){++consumerProtocols;protocol(cycle,ch,why);}};
 auto progress=[&](bool final){std::ofstream p(directory+"/progress.json");p<<"{\"cycles\":"<<cycles<<",\"source_ticks\":"<<ticks<<",\"requested_source_ticks\":"<<RW*RH*N<<",\"mismatch_count\":"<<mismatches<<",\"protocol_error_count\":"<<protocols<<",\"final\":"<<(final?"true":"false")<<'}';};
 progress(false);
 for(uint64_t cycle=0;cycle<LIMIT;cycle++){
  bool reset=cycle<2;context->time(cycle*10);top->clk=0;top->reset=reset;top->cam_valid=top->cam_sof=top->cam_eol=0;top->cam_data=top->cam_frame_id=0;top->out_ready=15;top->event_ready=15;
  while(updateCursor<updates.size()&&updates[updateCursor].at<=cycle){const auto& update=updates[updateCursor++];unsigned base=update.ch*256;if(update.black>=0)bus.add(cycle,base+4,unsigned(update.black));if(update.setGain)for(unsigned g=0;g<4;g++)bus.add(cycle,base+8+g*4,update.gain[g]);bus.add(cycle,base+24,1);}
  while(faultCursor<edges.size()&&edges[faultCursor].at<=cycle){auto f=edges[faultCursor++];if(f.kind==2)bus.add(cycle,f.ch*256,3);else faultActive[f.ch][f.kind]+=f.delta;}
  for(unsigned ch=0;ch<4;ch++)if(faultActive[ch][0])top->out_ready&=~(1u<<ch);
  while(windowCursor<windows.size()&&cycle>=windows[windowCursor][1])++windowCursor;
  bool dense=windowCursor<windows.size()&&cycle>=windows[windowCursor][0];
  bool pixelTick=false;unsigned sourceFrame=0,x=0,y=0;
  if(cycle>=ORIGIN&&ticks<RW*RH*N){phase+=RATE;if(phase>=100000000ULL){phase-=100000000ULL;pixelTick=true;sourceFrame=ticks/(RW*RH);uint64_t pos=ticks%(RW*RH);x=pos%RW;y=pos/RW;++ticks;if(ticks==RW*RH*N)sourceEnd=cycle+1;}}
  if(pixelTick&&x<W&&y<H){for(unsigned ch=0;ch<4;ch++){
   unsigned value=input.pixel(ch,sourceFrame,x,y);Frame& f=frames[ch][sourceFrame];f.inputHash.word(value);++f.sourcePixels;
   if(x==0&&y==0){f.firstInput=cycle*10+5;f.sourceStarted=true;}
   preview(f.rawPreview,W,H,x,y,{value,value,value});bool gap=faultActive[ch][1]!=0;
   if(!gap){top->cam_valid|=1u<<ch;if(x==0&&y==0)top->cam_sof|=1u<<ch;if(x==W-1)top->cam_eol|=1u<<ch;}
   top->cam_data|=uint64_t(value)<<(ch*16);top->cam_frame_id|=uint64_t(sourceFrame)<<(ch*16);
  }}
  bus.drive(*top,cycle);top->eval();
  for(unsigned ch=0;ch<4;ch++){
   bool valid=(top->out_valid>>ch)&1,ready=(top->out_ready>>ch)&1,sof=(top->out_sof>>ch)&1,eol=(top->out_eol>>ch)&1;
   uint64_t value=wide36(top->out_data,ch);unsigned id=lane16(top->out_frame_id,ch),epoch=lane16(top->out_epoch,ch),version=lane16(top->out_config_version,ch);
   if(!reset&&held[ch]&&(!valid||value!=heldData[ch]||sof!=heldSof[ch]||eol!=heldEol[ch]||id!=heldFrame[ch]||epoch!=heldEpoch[ch]||version!=heldVersion[ch]))protocol(cycle,ch,"output_changed_while_stalled");
   held[ch]=!reset&&valid&&!ready;heldData[ch]=value;heldSof[ch]=sof;heldEol[ch]=eol;heldFrame[ch]=id;heldEpoch[ch]=epoch;heldVersion[ch]=version;
   if(valid&&ready&&!reset){
    if(id>=N){protocol(cycle,ch,"output_frame_out_of_range");continue;}consumerError(cycle,ch,consumers[ch][id].pixel(value,epoch,version,sof,eol));Frame& f=frames[ch][id];unsigned index=f.received;
    if(!f.settingsKnown||index>=OW*OH){protocol(cycle,ch,"unexpected_output_pixel");continue;}
    if(sof!=(index==0)||eol!=(index%OW==OW-1)||version!=f.version)protocol(cycle,ch,"frame_boundary_or_config_version");
    if(index==0){if(f.terminal>=0&&f.epoch!=epoch)protocol(cycle,ch,"terminal_output_epoch_mismatch");f.firstOutput=cycle*10+5;f.epoch=epoch;}else if(f.epoch!=epoch)protocol(cycle,ch,"mixed_epoch");
    oracles[ch].load(input,ch,id,f.settings);auto expected=oracles[ch].pixel(index%OW,index/OW);std::array<unsigned,3> actual={unsigned((value>>24)&4095),unsigned((value>>12)&4095),unsigned(value&4095)};
    // Equal checked prefixes have identical digest state. Fork the reference
    // hash on the first unequal component; every component is still computed
    // by the independent oracle and compared before this optimization applies.
    for(unsigned component=0;component<3;component++){if(!f.referenceDiverged&&actual[component]!=expected[component]){f.referenceHash=f.outputHash;f.referenceDiverged=true;}
     f.outputHash.word(actual[component]);if(f.referenceDiverged)f.referenceHash.word(expected[component]);if(actual[component]!=expected[component]){++f.mismatches;++mismatches;if(errorRows++<100)errors<<"{\"kind\":\"pixel\",\"channel\":"<<ch<<",\"frame\":"<<id<<",\"x\":"<<index%OW<<",\"y\":"<<index/OW<<",\"component\":"<<component<<",\"expected\":"<<expected[component]<<",\"actual\":"<<actual[component]<<",\"cycle\":"<<cycle<<"}\n";}}
    preview(f.rgbPreview,OW,OH,index%OW,index/OW,actual);++f.received;f.lastOutput=cycle*10+5;
   }
   if(sourceFrame<N&&pixelTick)frames[ch][sourceFrame].fifoPeak=std::max(frames[ch][sourceFrame].fifoPeak,lane16(top->fifo_levels,ch));
   if(!reset&&(((top->event_valid&top->event_ready)>>ch)&1)){unsigned id=lane16(top->event_frame_id,ch);if(id>=N){protocol(cycle,ch,"terminal_frame_out_of_range");continue;}consumerError(cycle,ch,consumers[ch][id].finish((top->event_kind>>ch)&1,lane16(top->event_epoch,ch),lane16(top->event_config_version,ch),cycle*10+5));Frame& f=frames[ch][id];if(f.terminal>=0)protocol(cycle,ch,"duplicate_terminal");f.terminal=(top->event_kind>>ch)&1;f.reason=(top->event_reason>>(ch*8))&255;f.terminalTime=cycle*10+5;unsigned terminalEpoch=lane16(top->event_epoch,ch);if(lane16(top->event_config_version,ch)!=f.version)protocol(cycle,ch,"terminal_config_version_mismatch");if(f.received&&terminalEpoch!=f.epoch)protocol(cycle,ch,"terminal_output_epoch_mismatch");f.epoch=terminalEpoch;if(f.terminal==1&&f.received!=OW*OH)protocol(cycle,ch,"commit_without_complete_frame");events<<"{\"channel\":"<<ch<<",\"frame\":"<<id<<",\"cycle\":"<<cycle<<",\"time_ns\":"<<cycle*10+5<<",\"kind\":\""<<(f.terminal?"commit":"abort")<<"\",\"reason\":"<<f.reason<<"}\n";}
  }
  if(cycle%@TRACE_STRIDE@ULL==0||dense){
   trace<<cycle<<','<<cycle*10+5;for(unsigned ch=0;ch<4;ch++)trace<<','<<((top->cam_valid>>ch)&1)<<','<<((top->out_valid>>ch)&1)<<','<<((top->out_ready>>ch)&1)<<','<<lane16(top->fifo_levels,ch)<<','<<((top->event_valid>>ch)&1)<<','<<((top->event_kind>>ch)&1)<<','<<lane16(top->active_config_versions,ch);trace<<'\n';
  }
  if(dense)vcd.dump(cycle*10,*top);
  unsigned activeBefore=top->channel_active;Write applied{};bool wrote=!reset&&bus.sample(*top,applied);if(wrote&&top->s_axi_bresp){protocol(cycle,applied.addr/256,"csr_write_rejected");wrote=false;}
  context->time(cycle*10+5);top->clk=1;top->eval();if(dense)vcd.dump(cycle*10+5,*top);cycles=cycle+1;
  if(pixelTick&&x==0&&y==0)for(unsigned ch=0;ch<4;ch++){
   Frame& f=frames[ch][sourceFrame];f.admitted=!(activeBefore&(1u<<ch))&&(top->channel_active&(1u<<ch));
   if(f.admitted){if(hasPending[ch]){active[ch]=pending[ch];hasPending[ch]=false;++versions[ch];}f.settings=active[ch];f.version=versions[ch];f.settingsKnown=true;}
   else f.reason=(top->cam_valid&(1u<<ch))?6:7;
  }
  if(wrote){unsigned ch=applied.addr/256,reg=applied.addr%256;if(ch<4){if(reg==4)shadow[ch].black=applied.data;else if(reg>=8&&reg<=20)shadow[ch].gain[(reg-8)/4]=applied.data;else if(reg==24&&(applied.data&1)){pending[ch]=shadow[ch];hasPending[ch]=true;}}}
  if(cycles%100000000ULL==0){progress(false);std::cout<<"Progress cycles "<<cycles<<" source ticks "<<ticks<<std::endl;}
  if(context->gotFinish())return 4;
  if(sourceEnd&&!top->channel_active&&!top->out_valid&&!top->event_valid&&top->fifo_levels==0){if(++tail==64)break;}else tail=0;
 }
 progress(true);
 uint64_t published=0,discarded=0,lateDiscarded=0,abortedVisible=0;
 std::ofstream consumerManifest(directory+"/consumer.json");consumerManifest<<"{\"schema_version\":\"adas-transactional-consumer/1\",\"scope\":\"Actual accepted RGB and terminal handshakes; transactional hash/count receiver\",\"frames\":[";bool consumerComma=false;
 for(unsigned ch=0;ch<4;ch++)for(unsigned id=0;id<N;id++){
  auto& c=consumers[ch][id];published+=c.visible;discarded+=c.discarded;lateDiscarded+=c.lateDiscarded;abortedVisible+=c.abortedVisible;
  if(consumerComma)consumerManifest<<',';consumerComma=true;
  consumerManifest<<"{\"channel\":"<<ch<<",\"frame\":"<<id<<",\"status\":\""<<(c.visible?"published":c.terminal==0?"discarded":c.terminal==1?"rejected_commit":c.pixels?"staged":"unseen")<<"\",\"visible\":"<<(c.visible?"true":"false")<<",\"received_pixels\":"<<c.pixels<<",\"discarded_pixels\":"<<c.discarded<<",\"late_discarded_pixels\":"<<c.lateDiscarded<<",\"published_at_ns\":"<<c.publishedAt<<",\"epoch\":"<<c.epoch<<",\"config_version\":"<<c.version<<",\"published_sha256\":";
  if(c.visible)consumerManifest<<'"'<<c.stagedHash.hex()<<'"';else consumerManifest<<"null";consumerManifest<<'}';
 }
 consumerManifest<<"],\"published_frames\":"<<published<<",\"discarded_pixels\":"<<discarded<<",\"late_discarded_pixels\":"<<lateDiscarded<<",\"protocol_error_count\":"<<consumerProtocols<<",\"aborted_visible_frames\":"<<abortedVisible<<'}';
 uint64_t finalActivity=0;unsigned completed=0,aborted=0,unadmitted=0,missing=0;std::ofstream manifest(directory+"/frames.json");manifest<<"{\"frames\":[";bool comma=false;
 for(unsigned ch=0;ch<4;ch++)for(unsigned id=0;id<N;id++){
  auto& f=frames[ch][id];finalActivity=std::max(finalActivity,std::max(f.terminalTime,f.lastOutput));if(f.terminal==1)completed++;else if(f.terminal==0)aborted++;else if(f.sourceStarted&&!f.admitted)unadmitted++;else missing++;
  if(comma)manifest<<',';comma=true;std::string base="previews/ch"+std::to_string(ch)+"-f"+std::to_string(id);
  if(f.sourcePixels==W*H&&!f.rawPreview.empty())ppm(directory+'/'+base+"-raw.ppm",W,H,f.rawPreview);if(f.received&&!f.rgbPreview.empty())ppm(directory+'/'+base+"-rgb.ppm",OW,OH,f.rgbPreview);
  const char* status=f.terminal==1?"committed":f.terminal==0?"aborted":f.sourceStarted&&!f.admitted?"unadmitted":"missing";
  const char* reasons[]={"","fifo_overflow","channel_reset","frame_boundary","disabled","overlapping_frame","admission_rejected","input_sof_gap"};
  manifest<<"{\"channel\":"<<ch<<",\"frame\":"<<id<<",\"status\":\""<<status<<"\",\"input_sha256\":\""<<f.inputHash.hex()<<"\",\"output_sha256\":\""<<f.outputHash.hex()<<"\",\"reference_sha256\":\""<<(f.referenceDiverged?f.referenceHash:f.outputHash).hex()<<"\",\"source_pixels\":"<<f.sourcePixels<<",\"source_complete\":"<<(f.sourcePixels==W*H?"true":"false")<<",\"input_preview_artifact\":";
  if(f.sourcePixels==W*H&&!f.rawPreview.empty())manifest<<'"'<<base<<"-raw.ppm\"";else manifest<<"null";
  manifest<<",\"output_preview_artifact\":";
  if(f.received&&!f.rgbPreview.empty())manifest<<'"'<<base<<"-rgb.ppm\"";else manifest<<"null";
  manifest<<",\"first_input_ns\":"<<f.firstInput<<",\"first_output_ns\":"<<f.firstOutput<<",\"last_output_ns\":"<<f.lastOutput<<",\"terminal_ns\":"<<f.terminalTime<<",\"latency_ns\":"<<(f.terminalTime>=f.firstInput?f.terminalTime-f.firstInput:0)<<",\"received_pixels\":"<<f.received<<",\"mismatch_count\":"<<f.mismatches<<",\"fifo_peak_pixels\":"<<f.fifoPeak<<",\"config_version\":"<<f.version<<",\"epoch\":"<<f.epoch<<",\"terminal_output_epoch_checked\":"<<(f.received&&f.terminal>=0?"true":"false")<<",\"loss_reason\":\""<<(f.reason<8?reasons[f.reason]:"unknown")<<"\"}";
 }
 manifest<<"],\"channels\":[";for(unsigned ch=0;ch<4;ch++){unsigned committed=0,abort=0,lost=0;uint64_t errors=0;for(auto& f:frames[ch]){committed+=f.terminal==1;abort+=f.terminal==0;lost+=f.sourceStarted&&!f.admitted;errors+=f.mismatches;}if(ch)manifest<<',';manifest<<"{\"channel\":"<<ch<<",\"committed_frames\":"<<committed<<",\"aborted_frames\":"<<abort<<",\"unadmitted_frames\":"<<lost<<",\"fifo_peak_pixels\":"<<lane16(top->fifo_peaks,ch)<<",\"mismatch_count\":"<<errors<<",\"input_pixels\":"<<top->accepted_inputs[ch]<<",\"output_pixels\":"<<top->accepted_outputs[ch]<<'}';}manifest<<"]}";
 std::ofstream stats(directory+"/native-summary.json");stats<<"{\"completed_frames\":"<<completed<<",\"aborted_frames\":"<<aborted<<",\"unadmitted_frames\":"<<unadmitted<<",\"missing_frames\":"<<missing<<",\"mismatch_count\":"<<mismatches<<",\"protocol_error_count\":"<<protocols<<",\"consumer_published_frames\":"<<published<<",\"consumer_discarded_pixels\":"<<discarded<<",\"consumer_late_discarded_pixels\":"<<lateDiscarded<<",\"consumer_protocol_error_count\":"<<consumerProtocols<<",\"consumer_aborted_visible_frames\":"<<abortedVisible<<",\"raw_cache_bytes\":"<<input.cacheBytes()<<",\"raw_cache_frame_loads\":"<<input.loads<<",\"preview_buffer_bytes\":"<<previewBytes<<",\"cycles\":"<<cycles<<",\"source_end_cycle\":"<<sourceEnd<<",\"drain_ns\":"<<(sourceEnd&&finalActivity>sourceEnd*10?finalActivity-sourceEnd*10:0)<<",\"timed_out\":"<<(cycles>=LIMIT?"true":"false")<<'}';
 std::cout<<"Completed "<<completed<<" aborted "<<aborted<<" missing "<<missing<<" pixel mismatches "<<mismatches<<" protocol errors "<<protocols<<" cycles "<<cycles<<std::endl;top->final();return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<std::endl;return 3;}}
