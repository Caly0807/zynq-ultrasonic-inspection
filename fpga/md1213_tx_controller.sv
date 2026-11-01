`timescale 1ns/1ps

module md1213_tx_controller #(
    parameter longint unsigned CLK_HZ = 50_000_000,
    parameter int unsigned PULSE_WIDTH_NS = 100,
    parameter int unsigned OE_SETTLE_US = 1,
    parameter int unsigned MIN_TRIGGER_INTERVAL_US = 10_000
) (
    input  logic clk,
    input  logic reset_n,
    input  logic oe_enable_async,
    input  logic fire_req_async,
    output logic oe_out,
    output logic ina_out,
    output logic fire_ready,
    output logic busy,
    output logic fire_ack
);
    localparam longint unsigned PULSE_CYCLES_RAW =
        ((CLK_HZ * PULSE_WIDTH_NS) + 999_999_999) / 1_000_000_000;
    localparam longint unsigned PULSE_CYCLES =
        (PULSE_CYCLES_RAW < 1) ? 1 : PULSE_CYCLES_RAW;
    localparam longint unsigned OE_SETTLE_CYCLES_RAW =
        ((CLK_HZ * OE_SETTLE_US) + 999_999) / 1_000_000;
    localparam longint unsigned OE_SETTLE_CYCLES =
        (OE_SETTLE_CYCLES_RAW < 1) ? 1 : OE_SETTLE_CYCLES_RAW;
    localparam longint unsigned MIN_INTERVAL_CYCLES_RAW =
        ((CLK_HZ * MIN_TRIGGER_INTERVAL_US) + 999_999) / 1_000_000;
    localparam longint unsigned MIN_INTERVAL_CYCLES =
        (MIN_INTERVAL_CYCLES_RAW < 1) ? 1 : MIN_INTERVAL_CYCLES_RAW;

    localparam int PULSE_COUNTER_WIDTH =
        (PULSE_CYCLES <= 1) ? 1 : $clog2(PULSE_CYCLES + 1);
    localparam int OE_COUNTER_WIDTH =
        (OE_SETTLE_CYCLES <= 1) ? 1 : $clog2(OE_SETTLE_CYCLES + 1);
    localparam int INTERVAL_COUNTER_WIDTH =
        (MIN_INTERVAL_CYCLES <= 1) ? 1 : $clog2(MIN_INTERVAL_CYCLES + 1);

    logic oe_enable_meta;
    logic oe_enable_sync;
    logic fire_req_meta;
    logic fire_req_sync;
    logic fire_armed;
    logic oe_active;
    logic warmup_complete;
    logic pulse_active;
    logic controller_ready;
    logic ina_reg;
    logic [PULSE_COUNTER_WIDTH-1:0] pulse_remaining;
    logic [OE_COUNTER_WIDTH-1:0] warmup_elapsed;
    logic [INTERVAL_COUNTER_WIDTH-1:0] interval_remaining;

    assign oe_out = reset_n && oe_enable_async && oe_active;
    assign ina_out = reset_n && oe_enable_async && oe_active && ina_reg;
    assign busy = pulse_active;
    assign controller_ready = oe_enable_sync && oe_active && warmup_complete && !pulse_active && (interval_remaining == 0);
    assign fire_ready = reset_n && oe_enable_async && controller_ready;

    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            oe_enable_meta <= 1'b0;
            oe_enable_sync <= 1'b0;
            fire_req_meta <= 1'b0;
            fire_req_sync <= 1'b0;
        end else begin
            oe_enable_meta <= oe_enable_async;
            oe_enable_sync <= oe_enable_meta;
            fire_req_meta <= fire_req_async;
            fire_req_sync <= fire_req_meta;
        end
    end

    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            fire_armed <= 1'b0;
            oe_active <= 1'b0;
            warmup_complete <= 1'b0;
            pulse_active <= 1'b0;
            ina_reg <= 1'b0;
            fire_ack <= 1'b0;
            pulse_remaining <= '0;
            warmup_elapsed <= '0;
            interval_remaining <= '0;
        end else begin
            fire_ack <= 1'b0;

            if (interval_remaining != 0) begin
                interval_remaining <= interval_remaining - 1'b1;
            end

            if (!oe_enable_sync) begin
                fire_armed <= 1'b0;
                oe_active <= 1'b0;
                warmup_complete <= 1'b0;
                pulse_active <= 1'b0;
                ina_reg <= 1'b0;
                pulse_remaining <= '0;
                warmup_elapsed <= '0;
            end else begin
                if (!oe_active) begin
                    oe_active <= 1'b1;
                    warmup_complete <= 1'b0;
                    warmup_elapsed <= '0;
                end else if (!warmup_complete) begin
                    if ((OE_SETTLE_CYCLES <= 1) ||
                        (warmup_elapsed >= OE_SETTLE_CYCLES - 1)) begin
                        warmup_complete <= 1'b1;
                    end else begin
                        warmup_elapsed <= warmup_elapsed + 1'b1;
                    end
                end

                if (!fire_req_sync) begin
                    fire_armed <= 1'b1;
                end

                if (pulse_active) begin
                    if (pulse_remaining <= 1) begin
                        pulse_active <= 1'b0;
                        ina_reg <= 1'b0;
                        pulse_remaining <= '0;
                    end else begin
                        pulse_remaining <= pulse_remaining - 1'b1;
                    end
                end

                if (controller_ready && fire_req_sync && fire_armed) begin
                    fire_armed <= 1'b0;
                    fire_ack <= 1'b1;
                    pulse_active <= 1'b1;
                    ina_reg <= 1'b1;
                    pulse_remaining <= PULSE_CYCLES;
                    interval_remaining <= MIN_INTERVAL_CYCLES;
                end
            end
        end
    end
endmodule