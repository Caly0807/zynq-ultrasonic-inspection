`timescale 1ns/1ps

module tb_md1213_tx_controller;
    logic clk;
    logic reset_n;
    logic oe_enable_async;
    logic fire_req_async;
    logic oe_out;
    logic ina_out;
    logic fire_ready;
    logic busy;
    logic fire_ack;
    integer pulse_count;
    time oe_rise_time;
    time first_pulse_start;
    time second_pulse_start;
    time first_pulse_width;

    md1213_tx_controller #(
        .CLK_HZ(50_000_000),
        .PULSE_WIDTH_NS(100),
        .OE_SETTLE_US(1),
        .MIN_TRIGGER_INTERVAL_US(10_000)
    ) dut (
        .clk(clk),
        .reset_n(reset_n),
        .oe_enable_async(oe_enable_async),
        .fire_req_async(fire_req_async),
        .oe_out(oe_out),
        .ina_out(ina_out),
        .fire_ready(fire_ready),
        .busy(busy),
        .fire_ack(fire_ack)
    );

    always #10 clk = ~clk;

    always @(posedge oe_out) begin
        oe_rise_time = $time;
    end

    always @(posedge ina_out) begin
        pulse_count = pulse_count + 1;
        if (pulse_count == 1) begin
            first_pulse_start = $time;
        end else if (pulse_count == 2) begin
            second_pulse_start = $time;
        end
    end

    initial begin
        clk = 1'b0;
        reset_n = 1'b0;
        oe_enable_async = 1'b1;
        fire_req_async = 1'b1;
        pulse_count = 0;
        oe_rise_time = 0;
        first_pulse_start = 0;
        second_pulse_start = 0;
        first_pulse_width = 0;

        repeat (3) @(negedge clk);
        if (oe_out !== 1'b0 || ina_out !== 1'b0) begin
            $fatal(1, "outputs must stay low during reset");
        end

        reset_n = 1'b1;
        wait (fire_ready);
        repeat (5) @(posedge clk);
        if (pulse_count != 0 || fire_ack !== 1'b0) begin
            $fatal(1, "a request held through reset must not fire");
        end

        fire_req_async = 1'b0;
        repeat (5) @(negedge clk);
        fire_req_async = 1'b1;
        wait (fire_ack);
        wait (ina_out);
        first_pulse_start = $time;
        wait (!ina_out);
        first_pulse_width = $time - first_pulse_start;
        if (first_pulse_width != 100) begin
            $fatal(1, "expected a 100 ns pulse, got %0t ns", first_pulse_width);
        end
        if ((first_pulse_start - oe_rise_time) < 1_000) begin
            $fatal(1, "OE warm-up must be at least 1 us");
        end

        @(negedge clk);
        oe_enable_async = 1'b0;
        wait (!oe_out);
        repeat (20_000) @(posedge clk);
        @(negedge clk);
        oe_enable_async = 1'b1;
        wait (oe_out);
        wait (fire_ready);
        if (($time - oe_rise_time) < 1_000) begin
            $fatal(1, "OE warm-up must remain enforced after re-enable");
        end
        if (($time - first_pulse_start) < 10_000_000) begin
            $fatal(1, "cycling OE must not bypass the 100 Hz limit");
        end
        repeat (2) @(posedge clk);
        if (pulse_count != 1) begin
            $fatal(1, "a held request must produce only one pulse");
        end

        @(negedge clk);
        fire_req_async = 1'b0;
        repeat (5) @(negedge clk);
        fire_req_async = 1'b1;
        wait (fire_ack);
        wait (ina_out);
        #1;
        if (pulse_count != 2) begin
            $fatal(1, "second request was not accepted exactly once");
        end
        if ((second_pulse_start - first_pulse_start) < 10_000_000) begin
            $fatal(1, "pulse start interval must be at least 10 ms");
        end

        #20 reset_n = 1'b0;
        #1;
        if (oe_out !== 1'b0 || ina_out !== 1'b0 || busy !== 1'b0 || fire_ready !== 1'b0) begin
            $fatal(1, "reset must immediately force outputs and status low");
        end

        $display("PASS: reset interlock, stale-request rejection, OE warm-up, 100 ns pulse, one-shot behavior, 100 Hz limit, asynchronous reset clamp");
        $finish;
    end

    initial begin
        #30_000_000;
        $fatal(1, "controller test timed out");
    end
endmodule
