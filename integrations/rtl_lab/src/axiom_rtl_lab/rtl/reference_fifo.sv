`timescale 1ns/1ns
// Project-authored reference RTL. Not a vendor IP or a physical timing model.
module reference_fifo #(
    parameter int DEPTH = 64,
    localparam int PTR_W = $clog2(DEPTH),
    localparam int COUNT_W = $clog2(DEPTH + 1)
)(
    input logic clk, reset,
    input logic in_valid,
    output logic in_ready,
    input logic [31:0] in_data,
    output logic out_valid,
    input logic out_ready,
    output logic [31:0] out_data,
    output logic [COUNT_W-1:0] occupancy,
    output logic full, empty
);
    logic [31:0] storage [0:DEPTH-1];
    logic [PTR_W-1:0] rd, wr;
    logic push, pop;
    assign full = (occupancy == COUNT_W'(DEPTH));
    assign empty = (occupancy == 0);
    assign out_valid = !empty;
    assign out_data = empty ? 32'b0 : storage[rd];
    assign in_ready = !full || (out_valid && out_ready);
    assign push = in_valid && in_ready && !reset;
    assign pop = out_valid && out_ready && !reset;
    always_ff @(posedge clk) begin
        if (reset) begin
            occupancy <= 0;
            rd <= 0;
            wr <= 0;
        end else begin
            if (push) begin
                storage[wr] <= in_data;
                wr <= (wr == PTR_W'(DEPTH-1)) ? '0 : wr + 1'b1;
            end
            if (pop) rd <= (rd == PTR_W'(DEPTH-1)) ? '0 : rd + 1'b1;
            case ({push, pop})
                2'b10: occupancy <= occupancy + 1'b1;
                2'b01: occupancy <= occupancy - 1'b1;
                default: ;
            endcase
        end
    end
    if (DEPTH < (2**COUNT_W)-1) begin : check_capacity
        always_ff @(posedge clk) if (!reset)
            assert (occupancy <= COUNT_W'(DEPTH)) else $fatal(1, "FIFO capacity exceeded");
    end
endmodule
