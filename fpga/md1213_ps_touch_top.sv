`timescale 1ns/1ps
module md1213_ps_touch_top (
    inout wire [14:0] DDR_addr,
    inout wire [2:0] DDR_ba,
    inout wire DDR_cas_n, DDR_ck_n, DDR_ck_p, DDR_cke, DDR_cs_n,
    inout wire [3:0] DDR_dm,
    inout wire [31:0] DDR_dq,
    inout wire [3:0] DDR_dqs_n, DDR_dqs_p,
    inout wire DDR_odt, DDR_ras_n, DDR_reset_n, DDR_we_n,
    inout wire FIXED_IO_ddr_vrn, FIXED_IO_ddr_vrp,
    inout wire [53:0] FIXED_IO_mio,
    inout wire FIXED_IO_ps_clk, FIXED_IO_ps_porb, FIXED_IO_ps_srstb,
    input wire sys_clk, sys_rst_n,
    input wire [7:0] ad_data,
    input wire ad_otr,
    output wire ad_clk, da_clk,
    output wire [7:0] da_data,
    output wire md1213_oe, md1213_ina,
    output wire [1:0] led_n,
    output wire lcd_clk, lcd_de, lcd_hs, lcd_vs, lcd_bl,
    output wire [7:0] lcd_r, lcd_g, lcd_b,
    inout wire touch_scl, touch_sda, touch_rst_n, touch_int
);
    wire pixel_clk;
    wire video_locked;
    wire [23:0] video_data;
    wire touch_clock;
    wire touch_reset_n;
    wire [3:0] touch_input, touch_output, touch_tristate;
    wire tx_oe_internal, tx_ina_internal;
    assign md1213_oe = 1'b0;
    assign md1213_ina = 1'b0;
    assign lcd_r = video_data[23:16];
    assign lcd_b = video_data[15:8];
    assign lcd_g = video_data[7:0];
    assign led_n = {~video_locked, 1'b1};

    ODDR #(.DDR_CLK_EDGE("SAME_EDGE")) adc_clock_forward (
        .C(pixel_clk), .CE(1'b1), .D1(1'b1), .D2(1'b0), .Q(ad_clk), .R(1'b0), .S(1'b0)
    );
    ODDR #(.DDR_CLK_EDGE("SAME_EDGE")) dac_clock_forward (
        .C(pixel_clk), .CE(1'b1), .D1(1'b1), .D2(1'b0), .Q(da_clk), .R(1'b0), .S(1'b0)
    );
    ODDR #(.DDR_CLK_EDGE("SAME_EDGE")) lcd_clock_forward (
        .C(pixel_clk), .CE(1'b1), .D1(1'b0), .D2(1'b1), .Q(lcd_clk), .R(1'b0), .S(1'b0)
    );
    md1213_touch_io touch_io (
        .clock(touch_clock), .reset_n(touch_reset_n & sys_rst_n),
        .gpio_output(touch_output), .gpio_tristate(touch_tristate), .gpio_input(touch_input),
        .touch_scl(touch_scl), .touch_sda(touch_sda), .touch_rst_n(touch_rst_n), .touch_int(touch_int)
    );

    system_wrapper system (
        .DDR_addr(DDR_addr), .DDR_ba(DDR_ba), .DDR_cas_n(DDR_cas_n),
        .DDR_ck_n(DDR_ck_n), .DDR_ck_p(DDR_ck_p), .DDR_cke(DDR_cke), .DDR_cs_n(DDR_cs_n),
        .DDR_dm(DDR_dm), .DDR_dq(DDR_dq), .DDR_dqs_n(DDR_dqs_n), .DDR_dqs_p(DDR_dqs_p),
        .DDR_odt(DDR_odt), .DDR_ras_n(DDR_ras_n), .DDR_reset_n(DDR_reset_n), .DDR_we_n(DDR_we_n),
        .FIXED_IO_ddr_vrn(FIXED_IO_ddr_vrn), .FIXED_IO_ddr_vrp(FIXED_IO_ddr_vrp),
        .FIXED_IO_mio(FIXED_IO_mio), .FIXED_IO_ps_clk(FIXED_IO_ps_clk),
        .FIXED_IO_ps_porb(FIXED_IO_ps_porb), .FIXED_IO_ps_srstb(FIXED_IO_ps_srstb),
        .sys_clk(sys_clk), .sys_rst_n(sys_rst_n), .pixel_clk(pixel_clk),
        .ad_data(ad_data), .ad_otr(ad_otr), .gain_code(da_data),
        .md1213_oe(tx_oe_internal), .md1213_ina(tx_ina_internal),
        .video_data(video_data), .video_de(lcd_de), .video_hsync(lcd_hs),
        .video_vsync(lcd_vs), .video_locked(video_locked), .lcd_bl(lcd_bl),
        .touch_clock(touch_clock), .touch_reset_n(touch_reset_n),
        .touch_input(touch_input), .touch_output(touch_output), .touch_tristate(touch_tristate)
    );
endmodule
