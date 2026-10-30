`timescale 1ns/1ps
module tb_md1213_touch_io;
    reg clock = 0;
    reg reset_n = 0;
    reg [3:0] gpio_output = 0;
    reg [3:0] gpio_tristate = 4'hf;
    wire [3:0] gpio_input;
    tri1 touch_scl, touch_sda, touch_rst_n, touch_int;
    reg slave_data_low = 0;
    reg slave_interrupt_low = 0;
    reg test_passed = 0;
    assign touch_sda = slave_data_low ? 1'b0 : 1'bz;
    assign touch_int = slave_interrupt_low ? 1'b0 : 1'bz;
    always #10 clock = ~clock;

    md1213_touch_io dut (
        .clock(clock), .reset_n(reset_n), .gpio_output(gpio_output), .gpio_tristate(gpio_tristate), .gpio_input(gpio_input),
        .touch_scl(touch_scl), .touch_sda(touch_sda), .touch_rst_n(touch_rst_n), .touch_int(touch_int)
    );

    task settle;
        repeat (3) @(posedge clock);
        #1;
    endtask

    initial begin
        settle();
        if ({touch_int, touch_rst_n, touch_sda, touch_scl} !== 4'hf) $fatal(1, "Reset must release all pads");
        reset_n = 1;
        settle();
        if (gpio_input !== 4'hf) $fatal(1, "Released input synchronization failed");
        gpio_tristate = 4'hc;
        settle();
        if ({touch_sda, touch_scl} !== 2'b00 || gpio_input[1:0] !== 2'b00) $fatal(1, "I2C low drive failed");
        gpio_output = 4'h3;
        settle();
        if ({touch_sda, touch_scl} !== 2'b11) $fatal(1, "I2C high output must become high impedance");
        slave_data_low = 1;
        settle();
        if (touch_sda !== 1'b0 || gpio_input[1] !== 1'b0) $fatal(1, "I2C slave must override released SDA without contention");
        slave_data_low = 0;
        gpio_tristate = 4'hb;
        gpio_output = 0;
        settle();
        if (touch_rst_n !== 1'b0) $fatal(1, "Touch reset low failed");
        gpio_output = 4'h4;
        settle();
        if (touch_rst_n !== 1'b1) $fatal(1, "Touch reset high failed");
        gpio_tristate = 4'h3;
        gpio_output = 4'hc;
        settle();
        if (touch_int !== 1'b1) $fatal(1, "INT address select high failed");
        gpio_tristate = 4'hb;
        slave_interrupt_low = 1;
        settle();
        if (touch_int !== 1'b0 || gpio_input[3] !== 1'b0) $fatal(1, "Released INT input failed");
        slave_interrupt_low = 0;
        gpio_tristate = 0;
        gpio_output = 0;
        reset_n = 0;
        #1;
        if ({touch_int, touch_rst_n, touch_sda, touch_scl} !== 4'hf) $fatal(1, "System reset must override GPIO output enables immediately");
        settle();
        test_passed = 1;
        $display("TOUCH_IO_SIMULATION_PASSED");
        $finish;
    end
    initial begin
        #100000;
        $fatal(1, "Touch IO simulation watchdog expired");
    end
endmodule
