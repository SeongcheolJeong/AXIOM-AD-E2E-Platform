#include "Vreference_fifo.h"
#include "verilated.h"
#include "verilated_vcd_c.h"
#include <fstream>
#include <iostream>
#include <memory>
#include <sstream>
#include <string>
int main(int argc, char** argv) {
    if (argc != 4) { std::cerr << "stimulus.csv trace.csv fifo.vcd required\n"; return 2; }
    auto context = std::make_unique<VerilatedContext>();
    context->commandArgs(argc, argv); context->traceEverOn(true);
    auto top = std::make_unique<Vreference_fifo>(context.get());
    auto vcd = std::make_unique<VerilatedVcdC>(); top->trace(vcd.get(), 3); vcd->open(argv[3]);
    std::ifstream input(argv[1]); std::ofstream output(argv[2]);
    if (!input || !output || !vcd->isOpen()) return 3;
    output << "cycle,time_ns,reset,in_valid,in_ready,in_data,out_valid,out_ready,out_data,occupancy,full,empty,accepted_in,accepted_out,occupancy_after\n";
    std::string line; std::getline(input, line);
    while (std::getline(input, line)) {
        for (auto &c : line) if (c == ',') c = ' ';
        std::istringstream row(line); unsigned cycle, reset, valid, data, ready;
        if (!(row >> cycle >> reset >> valid >> data >> ready)) return 4;
        context->time(cycle * 10ULL); top->clk = 0; top->reset = reset;
        top->in_valid = valid; top->in_data = data; top->out_ready = ready; top->eval();
        vcd->dump(context->time());
        auto ir = top->in_ready, ov = top->out_valid; auto od = top->out_data;
        auto n = top->occupancy; auto full = top->full, empty = top->empty;
        unsigned push = !reset && valid && ir, pop = !reset && ov && ready;
        context->time(cycle * 10ULL + 5); top->clk = 1; top->eval(); vcd->dump(context->time());
        output << cycle << ',' << context->time() << ',' << reset << ',' << valid << ',' << unsigned(ir)
          << ',' << data << ',' << unsigned(ov) << ',' << ready << ',' << od << ',' << unsigned(n)
          << ',' << unsigned(full) << ',' << unsigned(empty) << ',' << push << ',' << pop
          << ',' << unsigned(top->occupancy) << '\n';
        if (context->gotFinish()) return 5;
    }
    top->final(); vcd->close();
    return output.good() ? 0 : 6;
}
