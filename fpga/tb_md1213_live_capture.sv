`timescale 1ns/1ps
module tb_md1213_live_capture;
    logic aclk = 0, adc_clk = 0, aresetn = 0, adc_resetn = 0;
    always #10 aclk = !aclk;
    always #20 adc_clk = !adc_clk;
    logic [11:0] awaddr = 0, araddr = 0;
    logic awvalid = 0, awready, wvalid = 0, wready;
    logic [31:0] wdata = 0, rdata;
    logic [3:0] wstrb = 0;
    logic [1:0] bresp, rresp;
    logic bvalid, bready = 0, arvalid = 0, arready, rvalid, rready = 0;
    logic [31:0] stream_data;
    logic [3:0] stream_keep;
    logic stream_valid, stream_ready = 0, stream_last;
    logic [7:0] ad_data = 200, gain_code;
    logic ad_otr = 0, md1213_oe, md1213_ina, interrupt;
    logic video_locked = 0, video_underflow = 0;
    logic consume_stream = 0, expect_ramp = 1, pulse_seen = 0;
    logic previous_stall = 0, held_last;
    logic [31:0] held_data;
    integer cycle_count = 0, words_seen = 0, frames_seen = 0;
    logic [31:0] result;
    integer pulse_cycles = 0, pulse_count = 0;
    logic prior_ina = 0;

    md1213_capture_ip #(.MIN_TRIGGER_INTERVAL_US(10), .LIVE_GUARD(1), .TX_LEASE_US(400)) dut (
        .aclk(aclk), .aresetn(aresetn), .adc_clk(adc_clk), .adc_resetn(adc_resetn),
        .s_axi_awaddr(awaddr), .s_axi_awvalid(awvalid), .s_axi_awready(awready),
        .s_axi_wdata(wdata), .s_axi_wstrb(wstrb), .s_axi_wvalid(wvalid), .s_axi_wready(wready),
        .s_axi_bresp(bresp), .s_axi_bvalid(bvalid), .s_axi_bready(bready),
        .s_axi_araddr(araddr), .s_axi_arvalid(arvalid), .s_axi_arready(arready),
        .s_axi_rdata(rdata), .s_axi_rresp(rresp), .s_axi_rvalid(rvalid), .s_axi_rready(rready),
        .m_axis_tdata(stream_data), .m_axis_tkeep(stream_keep), .m_axis_tvalid(stream_valid),
        .m_axis_tready(stream_ready), .m_axis_tlast(stream_last),
        .ad_data(ad_data), .ad_otr(ad_otr), .gain_code(gain_code),
        .video_locked(video_locked), .video_underflow(video_underflow),
        .md1213_oe(md1213_oe), .md1213_ina(md1213_ina), .interrupt(interrupt)
    );

    task automatic write_register(input logic [11:0] address, input logic [31:0] value,
                                  input logic [3:0] strobe = 15, input bit data_first = 0,
                                  input logic [1:0] response = 0);
        fork
            begin
                if (data_first) repeat (4) @(posedge aclk);
                @(negedge aclk); awaddr = address; awvalid = 1;
                do @(posedge aclk); while (!awready);
                @(negedge aclk); awvalid = 0;
            end
            begin
                if (!data_first) repeat (4) @(posedge aclk);
                @(negedge aclk); wdata = value; wstrb = strobe; wvalid = 1;
                do @(posedge aclk); while (!wready);
                @(negedge aclk); wvalid = 0;
            end
        join
        wait (bvalid);
        repeat (3) begin
            @(posedge aclk);
            if (!bvalid || bresp !== response) $fatal(1, "AXI write response address=%h response=%b", address, bresp);
        end
        @(negedge aclk); bready = 1;
        @(posedge aclk);
        @(negedge aclk); bready = 0;
    endtask

    task automatic read_register(input logic [11:0] address, output logic [31:0] value,
                                 input logic [1:0] response = 0);
        logic [31:0] held;
        @(negedge aclk); araddr = address; arvalid = 1;
        do @(posedge aclk); while (!arready);
        @(negedge aclk); arvalid = 0;
        wait (rvalid); held = rdata;
        repeat (3) begin
            @(posedge aclk);
            if (!rvalid || rdata !== held || rresp !== response) $fatal(1, "AXI read stability/response address=%h", address);
        end
        value = held;
        @(negedge aclk); rready = 1;
        @(posedge aclk);
        @(negedge aclk); rready = 0;
    endtask

    always @(negedge aclk) begin
        cycle_count = cycle_count + 1;
        stream_ready = aresetn && consume_stream && (cycle_count % 7 >= 2);
    end

    always @(posedge aclk) begin
        if (aresetn) begin
            if (md1213_ina) begin
                pulse_seen = 1;
                pulse_cycles = pulse_cycles + 1;
                if (!prior_ina) pulse_count = pulse_count + 1;
            end
            prior_ina = md1213_ina;
            if (expect_ramp && (md1213_oe || md1213_ina)) $fatal(1, "Test mode emitted a transmitter control signal");
            if (previous_stall && (!stream_valid || stream_data !== held_data || stream_last !== held_last))
                $fatal(1, "AXI stream changed under backpressure");
            previous_stall = stream_valid && !stream_ready;
            held_data = stream_data;
            held_last = stream_last;
            if (stream_valid && stream_ready) begin
                if (stream_keep !== 4'hf || stream_last !== (words_seen == 1023))
                    $fatal(1, "TKEEP/TLAST mismatch word=%0d", words_seen);
                for (integer lane = 0; lane < 4; lane = lane + 1) begin
                    if (stream_data[lane * 8 +: 8] !== (expect_ramp ? 8'((words_seen * 4 + lane) % 256) : 8'd200))
                        $fatal(1, "Sample mismatch word=%0d lane=%0d data=%h", words_seen, lane, stream_data);
                end
                if (stream_last) begin frames_seen = frames_seen + 1; words_seen = 0; end
                else words_seen = words_seen + 1;
            end
        end else previous_stall = 0;
    end

    initial begin
        #3000000; $fatal(1, "Capture test timeout");
    end

    initial begin
        repeat (8) @(posedge aclk);
        @(negedge aclk); aresetn = 1; adc_resetn = 1;
        read_register(12'h000, result);
        if (result != 32'h4d443132 || md1213_oe || md1213_ina) $fatal(1, "Guarded identity/reset mismatch");
        read_register(12'h05c, result);
        if (result != 32'h554c5632) $fatal(1, "Missing safety capability");
        write_register(12'h004, 7, 1);
        repeat (100) @(posedge aclk);
        read_register(12'h008, result);
        if (!result[3] || md1213_oe || md1213_ina || frames_seen) $fatal(1, "Unleased trigger was accepted");
        write_register(12'h060, 32'h11111111, 15, 0, 2'b10);
        write_register(12'h060, 32'h554c5632, 1, 0, 2'b10);
        expect_ramp = 0;
        consume_stream = 1;
        write_register(12'h004, 32'h26, 1);
        wait (frames_seen == 1);
        read_register(12'h008, result);
        if (!result[11] || result[8] || result[7:6] || pulse_seen) $fatal(1, "Passive source/TX interlock mismatch");
        read_register(12'h024, result);
        if (result != 200) $fatal(1, "Passive capture did not sample the ADC input");
        expect_ramp = 1;
        write_register(12'h004, 32'h16, 1);
        wait (frames_seen == 2);
        read_register(12'h008, result);
        if (!result[8] || result[11] || pulse_seen) $fatal(1, "TEST source flags mismatch");
        expect_ramp = 0;
        write_register(12'h060, 32'h554c5632);
        write_register(12'h004, 1, 1);
        repeat (100) @(posedge aclk);
        if (!md1213_oe || md1213_ina) $fatal(1, "Leased arm did not settle safely");
        write_register(12'h004, 7, 1);
        wait (frames_seen == 3);
        read_register(12'h008, result);
        if (result[11] || result[8] || !result[2] || result[13]) $fatal(1, "Pulsed source/completion mismatch");
        if (pulse_count != 1 || pulse_cycles != 5) $fatal(1, "Expected exactly one 100ns pulse, count=%0d cycles=%0d", pulse_count, pulse_cycles);
        write_register(12'h004, 0, 1);
        write_register(12'h060, 0);
        if (md1213_oe || md1213_ina) $fatal(1, "Explicit revoke did not stop TX");
        write_register(12'h060, 32'h554c5632);
        write_register(12'h004, 1, 1);
        repeat (21000) @(posedge aclk);
        read_register(12'h008, result);
        if (md1213_oe || md1213_ina || result[12] || !result[13]) $fatal(1, "Deadman lease did not expire");
        write_register(12'h004, 7, 1);
        repeat (50) @(posedge aclk);
        if (pulse_count != 1 || frames_seen != 3) $fatal(1, "Expiry allowed another pulse/frame");
        consume_stream = 0;
        write_register(12'h060, 32'h554c5632);
        write_register(12'h004, 1, 1);
        repeat (100) @(posedge aclk);
        write_register(12'h004, 7, 1);
        wait (stream_valid);
        repeat (21000) @(posedge aclk);
        if (md1213_oe || md1213_ina) $fatal(1, "Backpressure prevented TX watchdog shutdown");
        read_register(12'h008, result);
        if (!result[13] || !result[0] || pulse_count != 2) $fatal(1, "Stalled frame watchdog evidence missing");
        consume_stream = 1;
        wait (frames_seen == 4);
        write_register(12'h004, 0, 1);
        write_register(12'h060, 32'h554c5632);
        write_register(12'h004, 1, 1);
        repeat (100) @(posedge aclk);
        #7 aresetn = 0;
        #1 if (md1213_oe || md1213_ina) $fatal(1, "Reset failed to mask TX immediately");
        $display("LIVE_CAPTURE_SIM_PASS: passive ADC, TEST separation, 2 single pulses, exact 100ns width, lease expiry, stalled DMA shutdown, reset interlock");
        $finish;
    end
endmodule
