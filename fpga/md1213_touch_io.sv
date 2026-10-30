`timescale 1ns/1ps
module md1213_touch_io (
    input wire clock,
    input wire reset_n,
    input wire [3:0] gpio_output,
    input wire [3:0] gpio_tristate,
    output wire [3:0] gpio_input,
    inout wire touch_scl,
    inout wire touch_sda,
    inout wire touch_rst_n,
    inout wire touch_int
);
    wire [3:0] pads;
    (* ASYNC_REG = "TRUE" *) reg [3:0] input_meta = 4'hf;
    (* ASYNC_REG = "TRUE" *) reg [3:0] input_sync = 4'hf;

    IOBUF scl_buffer (.I(1'b0), .T(gpio_tristate[0] | gpio_output[0] | ~reset_n), .O(pads[0]), .IO(touch_scl));
    IOBUF sda_buffer (.I(1'b0), .T(gpio_tristate[1] | gpio_output[1] | ~reset_n), .O(pads[1]), .IO(touch_sda));
    IOBUF reset_buffer (.I(gpio_output[2]), .T(gpio_tristate[2] | ~reset_n), .O(pads[2]), .IO(touch_rst_n));
    IOBUF interrupt_buffer (.I(gpio_output[3]), .T(gpio_tristate[3] | ~reset_n), .O(pads[3]), .IO(touch_int));

    always @(posedge clock) begin
        if (!reset_n) begin
            input_meta <= 4'hf;
            input_sync <= 4'hf;
        end else begin
            input_meta <= pads;
            input_sync <= input_meta;
        end
    end
    assign gpio_input = input_sync;
endmodule
