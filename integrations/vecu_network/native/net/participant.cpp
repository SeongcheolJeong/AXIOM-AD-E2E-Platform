// AXIOM functional bus participant. SIL Kit is MIT; dependencies retain licenses.
// The Python supervisor supplies virtual-step work, never fabricates received frames.
#include <silkit/SilKit.hpp>
#include <silkit/config/all.hpp>
#include <silkit/services/can/all.hpp>
#include <silkit/services/ethernet/all.hpp>
#include <silkit/services/orchestration/all.hpp>
#include <nlohmann/json.hpp>
#include <chrono>
#include <condition_variable>
#include <iostream>
#include <mutex>
#include <future>
#include <vector>
#include <unistd.h>
using json=nlohmann::json;
using namespace std::chrono_literals;
using namespace SilKit::Services;

int main(int argc,char**argv){
 try {
  if(argc!=3)throw std::runtime_error("usage: participant ROLE REGISTRY_URI");
  std::string role=argv[1];
  if(role!="perception"&&role!="adas"&&role!="brake"&&role!="restbus")throw std::runtime_error("unregistered role");
  auto config=SilKit::Config::ParticipantConfigurationFromString("{\"Logging\":{\"Sinks\":[]}}");
  auto participant=SilKit::CreateParticipant(config,role,argv[2]);
  auto life=participant->CreateLifecycleService({Orchestration::OperationMode::Coordinated});
  auto clock=life->CreateTimeSyncService();
  auto can=participant->CreateCanController("can0","CAN0");
  auto eth=participant->CreateEthernetController("eth0","ETH0");
  std::mutex mutex;std::condition_variable cv;
  int64_t ready=-1,allowed=-1,expected=0;bool stopping=false;
  json pending=json::array(),received=json::array(),acks=json::array();
  can->AddFrameHandler([&](auto*,const Can::CanFrameEvent& event){
   std::lock_guard<std::mutex> lock(mutex);
   received.push_back({{"bus","can"},{"can_id",event.frame.canId},{"data",std::vector<uint8_t>(event.frame.dataField.begin(),event.frame.dataField.end())},{"time_ns",event.timestamp.count()},{"receiver",role}});
  });
  can->AddFrameTransmitHandler([&](auto*,const Can::CanFrameTransmitEvent& event){
   std::lock_guard<std::mutex> lock(mutex);acks.push_back({{"bus","can"},{"status",static_cast<int>(event.status)},{"time_ns",event.timestamp.count()}});
  });
  eth->AddFrameHandler([&](auto*,const Ethernet::EthernetFrameEvent& event){
   std::lock_guard<std::mutex> lock(mutex);
   received.push_back({{"bus","ethernet"},{"data",std::vector<uint8_t>(event.frame.raw.begin(),event.frame.raw.end())},{"time_ns",event.timestamp.count()},{"receiver",role}});
  });
  eth->AddFrameTransmitHandler([&](auto*,const Ethernet::EthernetFrameTransmitEvent& event){
   std::lock_guard<std::mutex> lock(mutex);acks.push_back({{"bus","ethernet"},{"status",static_cast<int>(event.status)},{"time_ns",event.timestamp.count()}});
  });
  life->SetCommunicationReadyHandler([&](){can->SetBaudRate(500000,2000000,0);can->Start();eth->Activate();});
  clock->SetSimulationStepHandlerAsync([&](std::chrono::nanoseconds now,std::chrono::nanoseconds){
   std::lock_guard<std::mutex> lock(mutex);ready=now.count()/1000000;cv.notify_all();
  },1ms);
  auto final=life->StartLifecycle();
  std::cout<<json({{"ok",true},{"op","init"},{"role",role},{"pid",getpid()},{"engine","silkit/5.0.8"}}).dump()<<std::endl;
  std::string line;
  while(std::getline(std::cin,line)){
   auto request=json::parse(line);std::string op=request.at("op");
   if(op=="close"){
    {std::lock_guard<std::mutex> lock(mutex);stopping=true;cv.notify_all();}
    life->Stop("AXIOM completed");final.wait_for(3s);
    std::cout<<json({{"ok",true},{"op","close"}}).dump()<<std::endl;return 0;
   }
   if(op!="tick"||request.at("time_ms").get<int64_t>()!=expected)throw std::runtime_error("noncontiguous virtual time");
   std::unique_lock<std::mutex> lock(mutex);
   if(!cv.wait_for(lock,10s,[&]{return ready==expected;}))throw std::runtime_error("SIL Kit initial/step barrier timeout");
   auto outgoing=request.value("frames",json::array());lock.unlock();
   for(const auto& item:outgoing){
    auto bytes=item.at("data").get<std::vector<uint8_t>>();
    if(item.at("bus")=="can"){
     auto id=item.at("can_id").get<unsigned>();
     if(id>0x7ff||bytes.size()!=8)throw std::runtime_error("Classic CAN requires 11bit ID /8 bytes");
     Can::CanFrame f{};f.canId=id;f.dlc=8;f.dataField=bytes;can->SendFrame(f);
    }else if(item.at("bus")=="ethernet"){
     if(bytes.size()<60||bytes.size()>1514)throw std::runtime_error("unsupported Ethernet frame length");
     Ethernet::EthernetFrame f{};f.raw=bytes;eth->SendFrame(f);
    }else throw std::runtime_error("unsupported bus");
   }
   clock->CompleteSimulationStep();lock.lock();
   if(!cv.wait_for(lock,10s,[&]{return ready==expected+1;}))throw std::runtime_error("SIL Kit next-step barrier timeout");
   // All messages timestamped before the new step have arrived by this barrier.
   auto result=json({{"ok",true},{"op","tick"},{"time_ms",expected},{"receiver",role},{"frames",received},{"acks",acks}});
   received=json::array();acks=json::array();++expected;lock.unlock();
   std::cout<<result.dump()<<std::endl;
  }
  {std::lock_guard<std::mutex> lock(mutex);stopping=true;cv.notify_all();}
  life->Stop("input closed");return 1;
 }catch(const std::exception&e){std::cerr<<e.what()<<std::endl;return 2;}
}
