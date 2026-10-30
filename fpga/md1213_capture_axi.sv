`timescale 1ns/1ps

module md1213_capture_axi #(
    parameter longint unsigned CLK_HZ = 50_000_000,
    parameter int unsigned MIN_TRIGGER_INTERVAL_US = 10_000
) (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 aclk CLK",
       X_INTERFACE_PARAMETER = "XIL_INTERFACENAME aclk, ASSOCIATED_BUSIF S_AXI:M_AXIS, ASSOCIATED_RESET aresetn, FREQ_HZ 50000000" *)
    input logic aclk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 aresetn RST",
       X_INTERFACE_PARAMETER = "XIL_INTERFACENAME aresetn, POLARITY ACTIVE_LOW" *)
    input logic aresetn,
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 adc_clk CLK",
       X_INTERFACE_PARAMETER = "XIL_INTERFACENAME adc_clk, ASSOCIATED_RESET adc_resetn, FREQ_HZ 25000000" *)
    input logic adc_clk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 adc_resetn RST",
       X_INTERFACE_PARAMETER = "XIL_INTERFACENAME adc_resetn, POLARITY ACTIVE_LOW" *)
    input logic adc_resetn,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWADDR",
       X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_AXI, PROTOCOL AXI4LITE, DATA_WIDTH 32, ADDR_WIDTH 12" *)
    input logic [11:0] s_axi_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWVALID" *)
    input logic s_axi_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWREADY" *)
    output logic s_axi_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WDATA" *)
    input logic [31:0] s_axi_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WSTRB" *)
    input logic [3:0] s_axi_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WVALID" *)
    input logic s_axi_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WREADY" *)
    output logic s_axi_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BRESP" *)
    output logic [1:0] s_axi_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BVALID" *)
    output logic s_axi_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BREADY" *)
    input logic s_axi_bready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARADDR" *)
    input logic [11:0] s_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARVALID" *)
    input logic s_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARREADY" *)
    output logic s_axi_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RDATA" *)
    output logic [31:0] s_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RRESP" *)
    output logic [1:0] s_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RVALID" *)
    output logic s_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RREADY" *)
    input logic s_axi_rready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TDATA" *)
    output logic [31:0] m_axis_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TKEEP" *)
    output logic [3:0] m_axis_tkeep,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TVALID" *)
    output logic m_axis_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TREADY" *)
    input logic m_axis_tready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TLAST" *)
    output logic m_axis_tlast,
    input logic [7:0] ad_data,
    input logic ad_otr,
    input logic video_locked,
    input logic video_underflow,
    output logic [7:0] gain_code,
    output logic md1213_oe,
    output logic md1213_ina,
    (* X_INTERFACE_INFO = "xilinx.com:signal:interrupt:1.0 interrupt INTERRUPT",
       X_INTERFACE_PARAMETER = "XIL_INTERFACENAME interrupt, SENSITIVITY LEVEL_HIGH" *)
    output logic interrupt
);
    typedef enum logic [2:0] {IDLE, WAIT_FIRE, CAPTURE, FETCH, LOAD, SEND} state_t;
    state_t state;
    logic aw_pending, w_pending;
    logic [11:0] write_address;
    logic [31:0] write_data;
    logic [3:0] write_strobe;
    logic [31:0] read_value;
    logic read_valid_address;
    logic enable_tx, test_mode, irq_enable, done_flag, rejected_flag;
    logic otr_flag, gate_hit, video_underflow_flag;
    logic fire_request, fire_ready, fire_ack;
    logic [31:0] frame_sequence;
    logic [11:0] sample_index, gate_start, gate_end, frame_gate_start, frame_gate_end;
    logic [7:0] threshold, baseline, frame_threshold, frame_baseline;
    logic frame_test;
    logic [7:0] minimum, maximum, peak, gate_peak;
    logic [11:0] peak_index, gate_peak_index;
    logic [9:0] word_index;
    logic [31:0] memory_word;
    (* IOB = "TRUE" *) logic [7:0] adc_sample;
    (* IOB = "TRUE" *) logic adc_overrange;
    logic adc_toggle;
    logic [7:0] sample_meta, sample_sync;
    logic otr_meta, otr_sync, toggle_meta, toggle_sync, toggle_seen;
    (* ASYNC_REG = "TRUE" *) logic locked_meta, locked_sync;
    (* ASYNC_REG = "TRUE" *) logic underflow_meta, underflow_sync;
    wire sample_tick = toggle_sync != toggle_seen;
    wire [7:0] capture_data = frame_test ? sample_index[7:0] : sample_sync;
    wire [7:0] magnitude = capture_data >= frame_baseline ?
        capture_data - frame_baseline : frame_baseline - capture_data;
    wire capture_write = state == CAPTURE && sample_tick;
    wire write_commit = aw_pending && w_pending && !s_axi_bvalid;
    wire control_write = write_commit && write_address == 12'h004 && write_strobe[0];
    wire start_command = control_write && write_data[1];
    wire clear_command = control_write && write_data[2];

    assign s_axi_awready = !aw_pending && !s_axi_bvalid;
    assign s_axi_wready = !w_pending && !s_axi_bvalid;
    assign s_axi_arready = !s_axi_rvalid;
    assign m_axis_tvalid = state == SEND;
    assign m_axis_tlast = state == SEND && word_index == 10'd1023;
    assign m_axis_tkeep = 4'hf;
    assign interrupt = irq_enable && done_flag;

    md1213_tx_controller #(
        .CLK_HZ(CLK_HZ), .PULSE_WIDTH_NS(100), .OE_SETTLE_US(1),
        .MIN_TRIGGER_INTERVAL_US(MIN_TRIGGER_INTERVAL_US)
    ) transmitter (
        .clk(aclk), .reset_n(aresetn), .oe_enable_async(enable_tx),
        .fire_req_async(fire_request), .oe_out(md1213_oe), .ina_out(md1213_ina),
        .fire_ready(fire_ready), .busy(), .fire_ack(fire_ack)
    );

    xpm_memory_sdpram #(
        .ADDR_WIDTH_A(12), .ADDR_WIDTH_B(10), .BYTE_WRITE_WIDTH_A(8),
        .CLOCKING_MODE("common_clock"), .MEMORY_PRIMITIVE("block"),
        .MEMORY_SIZE(32768), .WRITE_DATA_WIDTH_A(8), .READ_DATA_WIDTH_B(32),
        .READ_LATENCY_B(1), .WRITE_MODE_B("read_first"),
        .MEMORY_INIT_FILE("none"), .USE_MEM_INIT(0), .SIM_ASSERT_CHK(1)
    ) capture_memory (
        .clka(aclk), .ena(capture_write), .wea(capture_write),
        .addra(sample_index), .dina(capture_data),
        .clkb(aclk), .enb(state == FETCH), .addrb(word_index), .doutb(memory_word),
        .regceb(1'b1), .rstb(!aresetn), .sleep(1'b0),
        .injectsbiterra(1'b0), .injectdbiterra(1'b0), .sbiterrb(), .dbiterrb()
    );

    always_ff @(posedge adc_clk) begin
        adc_sample <= ad_data;
        adc_overrange <= ad_otr;
    end

    always_ff @(posedge adc_clk or negedge adc_resetn) begin
        if (!adc_resetn) adc_toggle <= 1'b0;
        else adc_toggle <= !adc_toggle;
    end

    always_ff @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            sample_meta <= '0;
            sample_sync <= '0;
            otr_meta <= 1'b0;
            otr_sync <= 1'b0;
            toggle_meta <= 1'b0;
            toggle_sync <= 1'b0;
            toggle_seen <= 1'b0;
            locked_meta <= 1'b0;
            locked_sync <= 1'b0;
            underflow_meta <= 1'b0;
            underflow_sync <= 1'b0;
        end else begin
            sample_meta <= adc_sample;
            sample_sync <= sample_meta;
            otr_meta <= adc_overrange;
            otr_sync <= otr_meta;
            toggle_meta <= adc_toggle;
            toggle_sync <= toggle_meta;
            toggle_seen <= toggle_sync;
            locked_meta <= video_locked;
            locked_sync <= locked_meta;
            underflow_meta <= video_underflow;
            underflow_sync <= underflow_meta;
        end
    end

    always_comb begin
        read_valid_address = 1'b1;
        case (s_axi_araddr)
            12'h000: read_value = 32'h4d443131;
            12'h004: read_value = {27'd0, test_mode, 3'd0, enable_tx};
            12'h008: read_value = {21'd0, video_underflow_flag, locked_sync, frame_test,
                md1213_ina, md1213_oe, gate_hit, otr_flag, rejected_flag, done_flag,
                (fire_ready && state == IDLE), (state != IDLE)};
            12'h00c: read_value = 32'd4096;
            12'h010: read_value = {24'd0, gain_code};
            12'h014: read_value = {31'd0, irq_enable};
            12'h018: read_value = {31'd0, done_flag};
            12'h01c: read_value = frame_sequence;
            12'h020: read_value = {23'd0, otr_sync, sample_sync};
            12'h024: read_value = {24'd0, minimum};
            12'h028: read_value = {24'd0, maximum};
            12'h02c: read_value = {24'd0, peak};
            12'h030: read_value = {20'd0, peak_index};
            12'h034: read_value = {20'd0, gate_start};
            12'h038: read_value = {20'd0, gate_end};
            12'h03c: read_value = {24'd0, threshold};
            12'h040: read_value = {24'd0, baseline};
            12'h044: read_value = {24'd0, gate_peak};
            12'h048: read_value = {20'd0, gate_peak_index};
            12'h04c: read_value = 32'd40;
            12'h050: read_value = 32'd4096;
            12'h054: read_value = 32'd100;
            12'h058: read_value = MIN_TRIGGER_INTERVAL_US;
            default: begin read_value = '0; read_valid_address = 1'b0; end
        endcase
    end

    always_ff @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            aw_pending <= 1'b0;
            w_pending <= 1'b0;
            write_address <= '0;
            write_data <= '0;
            write_strobe <= '0;
            s_axi_bvalid <= 1'b0;
            s_axi_bresp <= 2'b00;
            s_axi_rvalid <= 1'b0;
            s_axi_rdata <= '0;
            s_axi_rresp <= 2'b00;
            enable_tx <= 1'b0;
            test_mode <= 1'b0;
            gain_code <= 8'd0;
            irq_enable <= 1'b0;
            gate_start <= 12'd200;
            gate_end <= 12'd4095;
            threshold <= 8'd32;
            baseline <= 8'd128;
        end else begin
            if (s_axi_awvalid && s_axi_awready) begin
                write_address <= s_axi_awaddr;
                aw_pending <= 1'b1;
            end
            if (s_axi_wvalid && s_axi_wready) begin
                write_data <= s_axi_wdata;
                write_strobe <= s_axi_wstrb;
                w_pending <= 1'b1;
            end
            if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 1'b0;
            if (write_commit) begin
                aw_pending <= 1'b0;
                w_pending <= 1'b0;
                s_axi_bvalid <= 1'b1;
                s_axi_bresp <= 2'b00;
                case (write_address)
                    12'h004: if (write_strobe[0]) begin
                        enable_tx <= write_data[0] && !write_data[4];
                        test_mode <= write_data[4];
                    end
                    12'h010: if (write_strobe[0]) begin
                        if (write_data[7:0] <= 8'he0 && state == IDLE)
                            gain_code <= write_data[7:0];
                        else s_axi_bresp <= 2'b10;
                    end
                    12'h014: if (write_strobe[0]) irq_enable <= write_data[0];
                    12'h034: begin
                        if (write_strobe[0]) gate_start[7:0] <= write_data[7:0];
                        if (write_strobe[1]) gate_start[11:8] <= write_data[11:8];
                    end
                    12'h038: begin
                        if (write_strobe[0]) gate_end[7:0] <= write_data[7:0];
                        if (write_strobe[1]) gate_end[11:8] <= write_data[11:8];
                    end
                    12'h03c: if (write_strobe[0]) threshold <= write_data[7:0];
                    12'h040: if (write_strobe[0]) baseline <= write_data[7:0];
                    default: s_axi_bresp <= 2'b10;
                endcase
            end
            if (s_axi_rvalid && s_axi_rready) s_axi_rvalid <= 1'b0;
            if (s_axi_arvalid && s_axi_arready) begin
                s_axi_rdata <= read_value;
                s_axi_rresp <= read_valid_address ? 2'b00 : 2'b10;
                s_axi_rvalid <= 1'b1;
            end
        end
    end

    always_ff @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            state <= IDLE;
            sample_index <= '0;
            word_index <= '0;
            m_axis_tdata <= '0;
            fire_request <= 1'b0;
            done_flag <= 1'b0;
            rejected_flag <= 1'b0;
            otr_flag <= 1'b0;
            gate_hit <= 1'b0;
            video_underflow_flag <= 1'b0;
            frame_sequence <= '0;
            frame_gate_start <= '0;
            frame_gate_end <= '0;
            frame_threshold <= '0;
            frame_baseline <= '0;
            frame_test <= 1'b0;
            minimum <= 8'hff;
            maximum <= '0;
            peak <= '0;
            peak_index <= '0;
            gate_peak <= '0;
            gate_peak_index <= '0;
        end else begin
            fire_request <= 1'b0;
            if (clear_command) begin
                done_flag <= 1'b0;
                rejected_flag <= 1'b0;
                video_underflow_flag <= 1'b0;
            end
            if (underflow_sync) video_underflow_flag <= 1'b1;
            case (state)
                IDLE: begin end
                WAIT_FIRE: begin
                    if (!enable_tx) begin
                        state <= IDLE;
                        rejected_flag <= 1'b1;
                    end else if (fire_ack) state <= CAPTURE;
                end
                CAPTURE: if (sample_tick) begin
                    if (!frame_test && otr_sync) otr_flag <= 1'b1;
                    if (capture_data < minimum) minimum <= capture_data;
                    if (capture_data > maximum) maximum <= capture_data;
                    if (magnitude > peak) begin
                        peak <= magnitude;
                        peak_index <= sample_index;
                    end
                    if (sample_index >= frame_gate_start && sample_index <= frame_gate_end) begin
                        if (magnitude > gate_peak) begin
                            gate_peak <= magnitude;
                            gate_peak_index <= sample_index;
                        end
                        if (magnitude >= frame_threshold) gate_hit <= 1'b1;
                    end
                    if (sample_index == 12'd4095) begin
                        word_index <= '0;
                        state <= FETCH;
                    end else sample_index <= sample_index + 1'b1;
                end
                FETCH: state <= LOAD;
                LOAD: begin
                    m_axis_tdata <= memory_word;
                    state <= SEND;
                end
                SEND: if (m_axis_tready) begin
                    if (word_index == 10'd1023) begin
                        state <= IDLE;
                        done_flag <= 1'b1;
                        frame_sequence <= frame_sequence + 1'b1;
                    end else begin
                        word_index <= word_index + 1'b1;
                        state <= FETCH;
                    end
                end
                default: state <= IDLE;
            endcase
            if (start_command) begin
                if (state == IDLE && (write_data[4] || (write_data[0] && fire_ready)) &&
                    gate_start <= gate_end) begin
                    state <= write_data[4] ? CAPTURE : WAIT_FIRE;
                    fire_request <= !write_data[4];
                    frame_test <= write_data[4];
                    frame_gate_start <= gate_start;
                    frame_gate_end <= gate_end;
                    frame_threshold <= threshold;
                    frame_baseline <= baseline;
                    sample_index <= '0;
                    minimum <= 8'hff;
                    maximum <= '0;
                    peak <= '0;
                    peak_index <= '0;
                    gate_peak <= '0;
                    gate_peak_index <= '0;
                    gate_hit <= 1'b0;
                    otr_flag <= 1'b0;
                    done_flag <= 1'b0;
                end else rejected_flag <= 1'b1;
            end
        end
    end
endmodule
