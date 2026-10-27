// JSONL boundary around actual autoas Com/PduR/CanIf C implementations.
// SPDX-License-Identifier: GPL-3.0-only
#include "bridge.h"
#include "vendor/nlohmann/json.hpp"
#include <array>
#include <iostream>
#include <stdexcept>
#include <string>
using nlohmann::json;
static json events = json::array(), frames = json::array();
static uint64_t time_ms = 0, epoch = 0;
extern "C" void ax_trace(const char *module, const char *event, unsigned id) {
  events.push_back({{"module", module}, {"event", event}, {"pdu_id", id},
                    {"time_ms", time_ms}, {"epoch", epoch}});
}
extern "C" void ax_frame(unsigned can_id, const uint8_t *data) {
  frames.push_back({{"can_id", can_id}, {"data", std::vector<uint8_t>(data, data + 8)},
                    {"time_ms", time_ms}, {"epoch", epoch}});
}
static uint64_t integer(const json &j, const char *name, uint64_t maximum) {
  if (!j.contains(name) || !j[name].is_number_integer() || j[name].is_boolean() ||
      (j[name].is_number_integer() && j[name].get<int64_t>() < 0) ||
      j[name].get<uint64_t>() > maximum) throw std::runtime_error(std::string("invalid ") + name);
  return j[name].get<uint64_t>();
}
static unsigned index_for(unsigned can_id) {
  constexpr std::array<unsigned,4> ids{0x100,0x101,0x200,0x201};
  for (unsigned i=0;i<ids.size();i++) if (ids[i] == can_id) return i;
  throw std::runtime_error("CAN ID is not registered");
}
static std::array<uint8_t,8> payload(const json &j) {
  if (!j.contains("data") || !j["data"].is_array() || j["data"].size()!=8)
    throw std::runtime_error("Classic CAN payload must contain exactly 8 bytes");
  std::array<uint8_t,8> data{};
  for (unsigned i=0;i<8;i++) {
    const auto &v=j["data"][i];
    if (!v.is_number_integer() || v.is_boolean() || v.get<int64_t>() < 0 || v.get<uint64_t>()>255)
      throw std::runtime_error("payload contains a non-byte value");
    data[i]=v.get<uint8_t>();
  }
  return data;
}
int main() {
  std::string line;
  bool initialized=false;
  while (std::getline(std::cin,line)) {
    events=json::array(); frames=json::array();
    json reply={{"protocol","axiom-bsw/1"},{"ok",false},{"signals",json::array()}};
    bool closing=false;
    try {
      if (line.size()>65536) throw std::runtime_error("request too large");
      auto request=json::parse(line);
      if (!request.is_object()) throw std::runtime_error("request must be an object");
      if (request.contains("id")) reply["id"]=request["id"];
      const std::string op=request.at("op").get<std::string>(); reply["op"]=op;
      if (op=="init") {
        if (initialized) throw std::runtime_error("already initialized; use reset");
        time_ms=0; epoch=0; ax_bsw_init(); initialized=true;
        reply["upstream"]={{"project","autoas/as"},{"commit","cc3709675ac30f7539c0710dfec6d579a5235841"}};
        reply["modules"]={"Com","PduR","CanIf"};
        reply["can_ids"]={0x100,0x101,0x200,0x201};
      } else {
        if (!initialized) throw std::runtime_error("init required");
        if (op=="tx" || op=="rx" || op=="read") {
          unsigned can_id=integer(request,"can_id",0x7ff), index=index_for(can_id);
          std::array<uint8_t,8> result{};
          int status=0;
          if (op=="read") status=ax_bsw_read(index,result.data());
          else {
            auto data=payload(request);
            status=(op=="tx") ? ax_bsw_tx(index,data.data()) : ax_bsw_rx(index,data.data(),result.data());
          }
          if (status!=0) throw std::runtime_error("BSW returned E_NOT_OK");
          if (op!="tx") reply["signals"].push_back({{"can_id",can_id},{"data",result},{"epoch",epoch}});
        } else if (op=="tick") {
          uint64_t count=request.contains("elapsed_ms") ? integer(request,"elapsed_ms",10000) : 1;
          if (count==0) throw std::runtime_error("elapsed_ms must be positive");
          for (uint64_t i=0;i<count;i++) { ++time_ms; if (time_ms%10==0) ax_bsw_tick(); }
        } else if (op=="reset") { ++epoch; ax_bsw_init(); }
        else if (op=="close") closing=true;
        else throw std::runtime_error("unsupported operation");
      }
      for (const auto &event:events) if (event["module"]=="Det") throw std::runtime_error("BSW DET error");
      reply["ok"]=true;
    } catch (const std::exception &error) { reply["error"]=error.what(); }
    reply["time_ms"]=time_ms; reply["epoch"]=epoch;
    reply["trace"]=events; reply["frames"]=frames;
    std::cout << reply.dump() << '\n' << std::flush;
    if (closing) return 0;
  }
  return 0;
}
