// Host-native reference ADAS controller. This is a bounded simulation policy,
// not production ECU software or a safety validated vehicle controller.
#include "vendor/nlohmann/json.hpp"
#include "sha256.hpp"
#include <algorithm>
#include <cmath>
#include <iostream>
#include <limits>
#include <memory>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>
#include <tuple>
#include <unistd.h>
#include <vector>

#ifndef AXIOM_RAMP_POLICY
#define AXIOM_RAMP_POLICY 0
#endif
using J = nlohmann::json;
constexpr bool ramp_policy = AXIOM_RAMP_POLICY != 0;
constexpr const char* version = ramp_policy ? "ramp-traffic-cpp/1" : "reference-traffic-cpp/1";
constexpr size_t max_message = 256*1024;
[[noreturn]] void invalid(const std::string& reason) { throw std::runtime_error(reason); }
bool finite(const J& v) { return v.is_number() && std::isfinite(v.get<double>()); }
double number(const J& value, const char* key) {
    if (!value.is_object() || !value.contains(key) || !finite(value.at(key))) invalid("DUT nonfinite or missing numeric value");
    return value.at(key).get<double>();
}
double bound(double value,double low,double high) { return std::max(low,std::min(high,value)); }
bool fields(const J& object,const std::set<std::string>& keys) {
    if (!object.is_object() || object.size()!=keys.size()) return false;
    for (auto& key:keys) if (!object.contains(key)) return false;
    return true;
}
bool nonempty(const J& value) { return value.is_string() && !value.get<std::string>().empty(); }
void state_check(const J& state) {
    for (auto key:{"x","y","z","heading_rad","speed_mps"}) number(state,key);
    if (!state.contains("id") || !state.at("id").is_string() || number(state,"speed_mps")<0 || number(state,"speed_mps")>100)
        invalid("DUT actor identity/speed invalid");
}
J validate_profile(const J& profile) {
    if (!fields(profile,{"schema_version","project_id","id","revision","name","note","parameters","created_at","author","sha256"}) ||
        profile.at("schema_version")!="hil-dut-profile/1" || !profile.at("revision").is_number_integer() ||
        profile.at("revision").get<int64_t>()<1 || !profile.at("note").is_string()) invalid("Unsupported frozen DUT profile identity");
    for (auto key:{"project_id","id","name","created_at"}) if (!nonempty(profile.at(key))) invalid("Unsupported frozen DUT profile identity");
    const auto& author=profile.at("author");
    if (!fields(author,{"user_label","role"}) || !nonempty(author.at("user_label")) || !nonempty(author.at("role")))
        invalid("Unsupported frozen DUT profile author");
    const auto& params=profile.at("parameters");
    if (!fields(params,{"max_object_age_ms","recovery_fresh_steps"}) || !params.at("max_object_age_ms").is_number_integer() ||
        !params.at("recovery_fresh_steps").is_number_integer()) invalid("DUT profile requires integer age/recovery");
    int64_t age=params.at("max_object_age_ms"), recovery=params.at("recovery_fresh_steps");
    if (age<50 || age>1000 || age%50 || recovery<1 || recovery>10) invalid("DUT profile requires age50..1000ms step50 and recovery1..10");
    J content=profile; content.erase("sha256");
    if (sha256(content.dump())!=profile.at("sha256")) invalid("Frozen DUT profile hash mismatch");
    return profile;
}
std::vector<double> lengths(const J& route) {
    if (!route.is_array() || route.size()<2 || route.size()>1000) invalid("DUT route exceeds bounded profile");
    std::vector<double> result{0.};
    for (const auto& point:route) for (auto key:{"x","y","z"}) number(point,key);
    for (size_t i=1;i<route.size();++i) result.push_back(result.back()+std::hypot(number(route[i],"x")-number(route[i-1],"x"), number(route[i],"y")-number(route[i-1],"y")));
    if (result.back()<=.1) invalid("A route needs at least two distinct points");
    return result;
}
J project(const J& point,const J& route,const std::vector<double>& cumulative,std::optional<double> previous={}) {
    using Candidate=std::tuple<double,double,size_t,double,double>;
    std::optional<Candidate> best;
    for (size_t i=0;i+1<route.size();++i) {
        const auto& a=route[i];const auto& b=route[i+1];
        double segment=cumulative[i+1]-cumulative[i];
        if (segment<1e-6 || (previous && (cumulative[i+1]<*previous-4 || cumulative[i]>*previous+40))) continue;
        double ax=number(a,"x"),ay=number(a,"y"),dx=number(b,"x")-ax,dy=number(b,"y")-ay;
        double f=bound(((number(point,"x")-ax)*dx+(number(point,"y")-ay)*dy)/(segment*segment),0,1);
        double x=ax+f*dx,y=ay+f*dy,error=std::hypot(number(point,"x")-x,number(point,"y")-y);
        Candidate candidate{error,cumulative[i]+f*segment,i,x,y};
        if (!best || candidate<*best) best=candidate;
    }
    if (!best) invalid("No route segment inside the continuity window");
    auto [error,progress,index,x,y]=*best;
    return J{{"error_m",error},{"progress_m",progress},{"segment",index},{"x",x},{"y",y},{"remaining_m",std::max(0.,cumulative.back()-progress)}};
}
J interpolate(const J& route,const std::vector<double>& cumulative,double progress) {
    auto cursor=std::upper_bound(cumulative.begin(),cumulative.end(),progress);
    size_t index=std::min(route.size()-2,static_cast<size_t>(std::max(ptrdiff_t(0),cursor-cumulative.begin()-1)));
    double span=cumulative[index+1]-cumulative[index];
    double f=span ? bound((progress-cumulative[index])/span,0.,1.) : 0.;
    J result;
    for (auto key:{"x","y","z"}) result[key]=number(route[index],key)+f*(number(route[index+1],key)-number(route[index],key));
    return result;
}
std::pair<J,J> feedback(const J& state,const J& route,const std::vector<double>& cumulative,double speed,
                       const J& physics,std::optional<double> previous,std::optional<double> previous_steering,bool stop) {
    double wheelbase=number(physics,"wheelbase_m"),max_steer=number(physics,"max_steer_angle_rad");
    double rear=physics.contains("rear_axle_offset_m") ? number(physics,"rear_axle_offset_m") : 0.;
    if (!std::isfinite(speed) || speed<0 || speed>12 || wheelbase<=0 || wheelbase>12 || max_steer<=0 ||
        max_steer>=std::acos(-1.)/2 || rear<0 || rear>wheelbase) invalid("Unsupported city controller speed/physics");
    J projection=project(state,route,cumulative,previous), steering=state;
    double heading=number(state,"heading_rad"),velocity=number(state,"speed_mps");
    steering["x"]=number(state,"x")-rear*std::cos(heading);
    steering["y"]=number(state,"y")-rear*std::sin(heading);
    J steering_projection=project(steering,route,cumulative, rear==0 && !previous_steering ? previous : previous_steering);
    double lookahead=bound(2.+velocity*.8,3.,8.);
    J target=interpolate(route,cumulative,std::min(cumulative.back(),number(steering_projection,"progress_m")+lookahead));
    double dx=number(target,"x")-number(steering,"x"),dy=number(target,"y")-number(steering,"y");
    double local_y=-std::sin(heading)*dx+std::cos(heading)*dy;
    double steer_angle=std::atan2(2*wheelbase*local_y,std::max(.01,dx*dx+dy*dy));
    double desired=std::min(speed,std::sqrt(2*1.5*std::max(0.,number(projection,"remaining_m")-.5)));
    double endpoint=std::hypot(number(state,"x")-number(route.back(),"x"),number(state,"y")-number(route.back(),"y"));
    bool reached=number(projection,"remaining_m")<=1. && endpoint<=2.;
    if (stop || reached) desired=0.;
    double error=desired-velocity;
    double throttle=desired>.05 && error>-.15 ? bound(.34*error+.12,0.,.6) : 0.;
    double brake=error<-.15 ? bound(-.6*error,0.,1.) : (desired<=.05 ? 1. : 0.);
    if (brake>0) throttle=0.;
    J control{{"throttle",throttle},{"brake",brake},{"steer",bound(steer_angle/max_steer,-1.,1.)}};
    projection.update(J{{"target",target},{"desired_speed_mps",desired},{"steering_reference",rear!=0 ? "rear_axle" : "actor_origin"},
        {"steering_point",J{{"x",steering.at("x")},{"y",steering.at("y")}}},
        {"steering_progress_m",steering_projection.at("progress_m")},{"steering_error_m",steering_projection.at("error_m")},
        {"goal_distance_m",endpoint},{"goal_reached",reached},
        {"state",desired==0 && velocity<=.2 ? "stopped" : desired==0 ? "braking" : "tracking"}});
    return {control,projection};
}

class Dut {
    J route,physics,profile=nullptr;
    std::vector<double> cumulative;
    double speed=0,max_age=.25;
    int recovery_steps=3;
    int64_t sequence=-1,object_sequence=-1,duplicates=0,missing=0;
    int fresh_steps=0;
    std::string packet_fingerprint;
    std::optional<double> previous,previous_steering;
    bool degraded=false,was_stale=false;
    std::optional<double> brake_start,recovery_start;
    double brake_from=0;
    J previous_control{{"throttle",0.},{"brake",0.},{"steer",0.}};
    J recovery_from;
public:
    explicit Dut(const J& init) {
        if (init.value("schema_version","")!="hil-dut-init/1" || init.value("op","")!="init") invalid("Unsupported DUT initialization");
        if (init.contains("dut_profile")) {
            profile=validate_profile(init.at("dut_profile"));
            max_age=profile.at("parameters").at("max_object_age_ms").get<double>()/1000.;
            recovery_steps=profile.at("parameters").at("recovery_fresh_steps").get<int>();
        }
        route=init.at("route");cumulative=lengths(route);
        speed=number(init,"target_speed_mps");
        if (speed<0 || speed>4) invalid("DUT speed exceeds reference profile");
        physics=init.at("physics");
    }
    J ready() const {
        J reply{{"schema_version","hil-dut-reply/1"},{"op","init"},{"status","ready"},{"pid",getpid()},{"dut_version",version}};
        if (!profile.is_null()) reply["dut_profile"]=profile;
        return reply;
    }
    J step(const J& message) {
        if (message.value("schema_version","")!="hil-dut-step/1" || message.value("op","")!="step" ||
            !message.contains("sequence") || !message.at("sequence").is_number_integer()) invalid("DUT sequence or 20 Hz simulation clock mismatch");
        int64_t seq=message.at("sequence");double now=number(message,"time_s");
        if (seq!=sequence+1 || std::abs(now-seq*.05)>1e-6) invalid("DUT sequence or 20 Hz simulation clock mismatch");
        const auto& ego=message.at("ego"); const auto& packet=message.at("object_packet");
        state_check(ego);
        if (!packet.contains("sequence") || !packet.at("sequence").is_number_integer()) invalid("DUT object packet identity/clock invalid");
        int64_t objseq=packet.at("sequence");double objtime=number(packet,"time_s");
        if (objseq<object_sequence || objseq>seq || std::abs(objtime-objseq*.05)>1e-6 || objtime>now+1e-8 ||
            !packet.contains("objects") || !packet.at("objects").is_array() || packet.at("objects").size()>32)
            invalid("DUT object packet identity/clock invalid");
        std::set<std::string> ids;
        for (const auto& object:packet.at("objects")) {
            state_check(object);std::string id=object.at("id");
            if (id==ego.at("id") || !ids.insert(id).second) invalid("DUT object identities duplicate ego or one another");
        }
        double age=std::max(0.,now-objtime);
        bool fresh=age<=max_age+1e-8,new_packet=objseq>object_sequence;
        J presence=message.value("object_delivery_present",J(true));
        if (!presence.is_boolean() || (!presence.get<bool>() && new_packet)) invalid("DUT delivery presence contradicts packet sequence");
        bool present=presence.get<bool>();std::string fingerprint=packet.dump();
        if (!new_packet && fingerprint!=packet_fingerprint) invalid("DUT repeated sequence changed packet contents");
        bool duplicate=present && !new_packet;
        duplicates+=duplicate;missing+=!present;packet_fingerprint=fingerprint;
        bool was_degraded=degraded;
        if (!fresh) { degraded=true;fresh_steps=0; }
        else if (degraded) {
            fresh_steps=new_packet ? fresh_steps+1 : 0;
            if (fresh_steps>=recovery_steps) degraded=false;
        }
        double desired=speed,origin=number(project(ego,route,cumulative),"progress_m");
        std::optional<std::pair<double,std::string>> nearest;
        for (const auto& object:packet.at("objects")) {
            J projected=project(object,route,cumulative);double gap=number(projected,"progress_m")-origin;
            std::pair<double,std::string> item{gap,object.at("id").get<std::string>()};
            if (gap>0 && gap<60 && number(projected,"error_m")<2.2 && (!nearest || item<*nearest)) nearest=item;
        }
        J lead=nullptr;
        if (nearest) { desired=std::min(speed,std::max(0.,(nearest->first-6)/1.5));lead=J{{"id",nearest->second},{"origin_gap_m",nearest->first}}; }
        auto [control,plan]=feedback(ego,route,cumulative,desired,physics,previous,previous_steering,degraded);
        std::string state=!fresh ? "degraded" : degraded ? "recovering" : "tracking";
        std::string phase="tracking";double phase_elapsed=0.;
        if constexpr (!ramp_policy) {
            if (degraded) control=J{{"throttle",0.},{"brake",1.},{"steer",control.at("steer")}};
        } else {
            if (!fresh && !was_stale) {
                brake_start=now;brake_from=number(previous_control,"brake");recovery_start.reset();
            }
            if (degraded) {
                if (!brake_start) { brake_start=now;brake_from=number(previous_control,"brake"); }
                phase_elapsed=std::max(0.,now-*brake_start);
                double alpha=bound(phase_elapsed/.3,0.,1.);
                control["throttle"]=0.;control["brake"]=brake_from+(1.-brake_from)*alpha;
                phase=fresh ? "recovering_gate" : phase_elapsed<.3-1e-9 ? "braking_ramp" : "degraded_hold";
            } else {
                if (was_degraded) { recovery_start=now;recovery_from=previous_control;brake_start.reset(); }
                if (recovery_start) {
                    phase_elapsed=std::max(0.,now-*recovery_start);
                    double alpha=bound(phase_elapsed/.5,0.,1.);
                    if (alpha<1.-1e-9) {
                        for (auto key:{"throttle","brake"}) control[key]=number(recovery_from,key)+(number(control,key)-number(recovery_from,key))*alpha;
                        // Keep brake/throttle mutually exclusive while the brake is being released.
                        if (number(control,"brake")>1e-9) control["throttle"]=0.;
                        phase="recovery_ramp";state="recovering";
                    } else recovery_start.reset();
                }
            }
        }
        plan.update(J{{"hil_state",state},{"object_age_s",age},{"lead",lead}});
        previous=number(plan,"progress_m");previous_steering=number(plan,"steering_progress_m");
        sequence=seq;object_sequence=objseq;previous_control=control;was_stale=!fresh;
        J reply{{"schema_version","hil-dut-reply/1"},{"op","step"},{"sequence",seq},{"object_sequence",objseq},
            {"object_age_s",age},{"state",state},{"duplicate_packet_detected",duplicate},{"duplicate_packet_count",duplicates},
            {"missing_packet_count",missing},{"fresh_recovery_steps",fresh_steps},{"control",control},{"plan",plan}};
        if constexpr (ramp_policy) { reply["policy_phase"]=phase;reply["phase_elapsed_s"]=phase_elapsed; }
        return reply;
    }
};
int main() {
    try {
        std::unique_ptr<Dut> dut;
        while (true) {
            // Bounded reads avoid allocating an attacker-controlled line before checking size.
            std::string raw;raw.reserve(4096);bool newline=false;
            for (int c;(c=std::cin.get())!=EOF;) {
                if (c=='\n') { newline=true;break; }
                raw.push_back(static_cast<char>(c));
                if (raw.size()>=max_message) invalid("DUT protocol message exceeds bound");
            }
            if (raw.empty() && !newline && std::cin.eof()) return 0;
            if (!newline) invalid("DUT protocol message exceeds bound");
            J message=J::parse(raw);J reply;
            if (!message.is_object()) invalid("DUT protocol message must be an object");
            std::string op=message.value("op","");
            if (op=="init" && !dut) { dut=std::make_unique<Dut>(message);reply=dut->ready(); }
            else if (op=="step" && dut) reply=dut->step(message);
            else if (op=="close" && dut) {
                std::cout<<J{{"schema_version","hil-dut-reply/1"},{"op","close"},{"status","closed"}}.dump()<<std::endl;
                return 0;
            } else invalid("DUT protocol state transition invalid");
            std::cout<<reply.dump()<<std::endl;
        }
    } catch (const std::exception& error) {
        std::cerr<<"native ECU error: "<<error.what()<<std::endl;
        return 2;
    }
}
