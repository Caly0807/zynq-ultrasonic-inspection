`timescale 1ns/1ps
module tb_md1213_capture_axi;
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

    md1213_capture_ip #(.MIN_TRIGGER_INTERVAL_US(10)) dut (
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
            if (md1213_ina) pulse_seen = 1;
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
        if (result !== 32'h4d443131) $fatal(1, "Identity mismatch");
        read_register(12'h004, result);
        if (result !== 0 || md1213_oe || md1213_ina) $fatal(1, "Unsafe reset defaults");
        read_register(12'hfff, result, 2'b10);
        write_register(12'hffc, 1, 15, 0, 2'b10);
        write_register(12'h010, 8'he0, 1, 1);
        write_register(12'h010, 8'he1, 1, 0, 2'b10);
        write_register(12'h010, 0, 0);
        if (gain_code !== 8'he0) $fatal(1, "Gain clamp or zero-strobe failure");
        write_register(12'h034, 100, 3);
        write_register(12'h038, 400, 3, 1);
        write_register(12'h03c, 127, 1);
        write_register(12'h014, 1, 1);
        write_register(12'h004, 32'h13, 1, 1);
        write_register(12'h004, 32'h12, 1);
        read_register(12'h008, result);
        if (!result[3] || !result[0]) $fatal(1, "Busy trigger was not rejected");
        wait (stream_valid);
        repeat (40) @(posedge aclk);
        if (interrupt || frames_seen) $fatal(1, "Completed before stream was accepted");
        consume_stream = 1;
        wait (interrupt);
        read_register(12'h01c, result);
        if (frames_seen != 1 || result != 1) $fatal(1, "First frame completion mismatch");
        read_register(12'h024, result); if (result != 0) $fatal(1, "Minimum mismatch");
        read_register(12'h028, result); if (result != 255) $fatal(1, "Maximum mismatch");
        read_register(12'h02c, result); if (result != 128) $fatal(1, "Peak mismatch");
        read_register(12'h044, result); if (result != 128) $fatal(1, "Gate peak mismatch");
        read_register(12'h048, result); if (result != 256) $fatal(1, "Gate index mismatch");
        read_register(12'h008, result); if (!result[5] || result[4]) $fatal(1, "Test gate/OTR mismatch");
        write_register(12'h004, 32'h14, 1);
        if (interrupt) $fatal(1, "Sticky completion did not clear");
        write_register(12'h004, 32'h12, 1);
        wait (frames_seen == 2);
        wait (interrupt);
        write_register(12'h004, 0, 1);
        expect_ramp = 0;
        ad_otr = 1;
        write_register(12'h004, 1, 1);
        repeat (100) @(posedge aclk);
        write_register(12'h004, 3, 1);
        wait (frames_seen == 3);
        read_register(12'h008, result);
        if (!result[4] || !pulse_seen) $fatal(1, "Real ADC / simulated transmitter path not exercised");
        read_register(12'h024, result); if (result != 200) $fatal(1, "ADC minimum mismatch");
        read_register(12'h028, result); if (result != 200) $fatal(1, "ADC maximum mismatch");
        video_locked = 1; video_underflow = 1;
        repeat (8) @(posedge aclk);
        video_underflow = 0;
        repeat (8) @(posedge aclk);
        read_register(12'h008, result);
        if (!result[9] || !result[10]) $fatal(1, "Video status CDC/sticky mismatch");
        write_register(12'h004, 4, 1);
        repeat (8) @(posedge aclk);
        if (md1213_oe || md1213_ina || interrupt) $fatal(1, "Disable/clear failed");
        read_register(12'h008, result); if (result[10]) $fatal(1, "Underflow did not clear");
        $display("CAPTURE_AXI_SIM_PASS: 3 frames / 12288 samples, independent AXI channels, backpressure, IRQ, statistics, test-mode TX interlock");
        $finish;
    end
endmodule
