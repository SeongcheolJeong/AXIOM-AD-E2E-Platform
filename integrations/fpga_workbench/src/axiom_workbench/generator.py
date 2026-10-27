"""Generate a trusted, bounded serial FIFO network; graph labels never become HDL identifiers."""
from .model import FIELDS

def generate(depths):
    n=len(depths)
    top=['`timescale 1ns/1ns',f'''module workbench_top (
 input logic clk, reset, in_valid, out_ready,
 input logic [31:0] in_data,
 output logic in_ready, out_valid,
 output logic [31:0] out_data,
 output logic [{n-1}:0] n_in_valid,n_in_ready,n_out_valid,n_out_ready,n_full,n_empty,
 output logic [31:0] n_in_data [{n}],n_out_data [{n}],n_occupancy [{n}]
);
 wire [{n}:0] valid_bus,ready_bus;
 wire [31:0] data_bus [{n+1}];
 assign valid_bus[0]=in_valid;
 assign data_bus[0]=in_data;
 assign in_ready=ready_bus[0];
 assign ready_bus[{n}]=out_ready;
 assign out_valid=valid_bus[{n}];
 assign out_data=data_bus[{n}];''']
    for i,depth in enumerate(depths):
        width=depth.bit_length()
        top.append(f''' wire [{width-1}:0] count_{i};
 reference_fifo #(.DEPTH({depth})) fifo_{i} (
 .clk(clk),.reset(reset),.in_valid(valid_bus[{i}]),.in_ready(ready_bus[{i}]),.in_data(data_bus[{i}]),
 .out_valid(valid_bus[{i+1}]),.out_ready(ready_bus[{i+1}]),.out_data(data_bus[{i+1}]),
 .occupancy(count_{i}),.full(n_full[{i}]),.empty(n_empty[{i}]));
 assign n_in_valid[{i}]=valid_bus[{i}]; assign n_in_ready[{i}]=ready_bus[{i}]; assign n_in_data[{i}]=data_bus[{i}];
 assign n_out_valid[{i}]=valid_bus[{i+1}]; assign n_out_ready[{i}]=ready_bus[{i+1}]; assign n_out_data[{i}]=data_bus[{i+1}];
 assign n_occupancy[{i}]=32'(count_{i});''')
    top.append('endmodule\n')
    header='cycle,time_ns,reset,in_valid,in_ready,in_data,out_valid,out_ready,out_data,accepted_in,accepted_out'+''.join(','+f'f{i}_{k}' for i in range(n) for k in FIELDS)
    cpp=r'''#include "Vworkbench_top.h"
#include "verilated.h"
#include "verilated_vcd_c.h"
#include <fstream>
#include <sstream>
#include <string>
#include <memory>
#include <array>
int main(int argc,char** argv) {
 if(argc!=4)return 2;
 auto context=std::make_unique<VerilatedContext>();context->commandArgs(argc,argv);context->traceEverOn(true);
 auto top=std::make_unique<Vworkbench_top>(context.get());
 auto vcd=std::make_unique<VerilatedVcdC>();top->trace(vcd.get(),4);vcd->open(argv[3]);
 std::ifstream input(argv[1]);std::ofstream output(argv[2]);if(!input||!output||!vcd->isOpen())return 3;
 output << "HEADER\n";
 std::string line;std::getline(input,line);
 while(std::getline(input,line)) {
  for(auto &ch:line)if(ch==',')ch=' ';
  std::istringstream row(line);unsigned cycle,reset,valid,data,ready;
  if(!(row>>cycle>>reset>>valid>>data>>ready))return 4;
  context->time(cycle*10ULL);top->clk=0;top->reset=reset;top->in_valid=valid;top->in_data=data;top->out_ready=ready;top->eval();vcd->dump(context->time());
  unsigned ir=top->in_ready,ov=top->out_valid,od=top->out_data;
  std::array<std::array<unsigned,12>,COUNT> nodes;
  for(unsigned i=0;i<COUNT;++i) {
   unsigned iv=(top->n_in_valid>>i)&1,irn=(top->n_in_ready>>i)&1,ovn=(top->n_out_valid>>i)&1,orr=(top->n_out_ready>>i)&1;
   nodes[i]={iv,irn,top->n_in_data[i],ovn,orr,top->n_out_data[i],top->n_occupancy[i],(top->n_full>>i)&1,(top->n_empty>>i)&1,
     unsigned(!reset&&iv&&irn),unsigned(!reset&&ovn&&orr),0};
  }
  context->time(cycle*10ULL+5);top->clk=1;top->eval();vcd->dump(context->time());
  output<<cycle<<','<<context->time()<<','<<reset<<','<<valid<<','<<ir<<','<<data<<','<<ov<<','<<ready<<','<<od<<','<<unsigned(!reset&&valid&&ir)<<','<<unsigned(!reset&&ov&&ready);
  for(unsigned i=0;i<COUNT;++i){nodes[i][11]=top->n_occupancy[i];for(auto value:nodes[i])output<<','<<value;}
  output<<'\n';if(context->gotFinish())return 5;
 }
 top->final();vcd->close();return output.good()?0:6;
}
'''.replace('HEADER',header).replace('COUNT',str(n))
    return {'generated_top.sv':'\n'.join(top),'harness.cpp':cpp}
