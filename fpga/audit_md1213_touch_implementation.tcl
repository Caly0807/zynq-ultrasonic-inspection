set script_dir [file dirname [file normalize [info script]]]
set project_dir [file join $script_dir build md1213_linux_touch]
open_project [file join $project_dir md1213_linux_touch.xpr]
open_run impl_1
set channel [open [file join $project_dir reports_touch implementation_audit.rpt] w]
proc audit_line {text} {global channel; puts $channel $text; puts $text}
proc is_ground_driven {pin} {
    set nets [get_nets -quiet -segments -of_objects $pin]
    set drivers [get_pins -quiet -leaf -of_objects $nets -filter {DIRECTION == OUT}]
    foreach driver $drivers {
        set cell [get_cells -quiet -of_objects $driver]
        if {[get_property REF_NAME $cell] eq "GND"} {return 1}
    }
    return 0
}
foreach name {md1213_oe md1213_ina} {
    set net [get_nets -of_objects [get_ports $name]]
    set drivers [get_pins -leaf -of_objects $net -filter {DIRECTION == OUT}]
    if {[llength $drivers] != 1} {error "UNEXPECTED_OUTPUT_DRIVERS $name"}
    set cell [get_cells -of_objects $drivers]
    if {[get_property REF_NAME $cell] ne "OBUF" || ![is_ground_driven [get_pins $cell/I]]} {error "HV_OUTPUT_NOT_HARD_GROUNDED $name"}
    audit_line "HV_PAD_CONSTANT_ZERO=$name"
}
foreach {name expected_pin} {touch_scl R19 touch_sda P20 touch_rst_n M19 touch_int U19} {
    set port [get_ports $name]
    if {[get_property PACKAGE_PIN $port] ne $expected_pin || [get_property IOSTANDARD $port] ne "LVCMOS33"} {error "TOUCH_PAD_CONSTRAINT_ERROR $name"}
    audit_line "TOUCH_PAD=$name PIN=$expected_pin STANDARD=LVCMOS33"
}
foreach name {scl_buffer sda_buffer} {
    set cell [get_cells -hier -filter "NAME =~ *touch_io/$name"]
    if {[llength $cell] != 1 || [get_property REF_NAME $cell] ne "IOBUF" || ![is_ground_driven [get_pins $cell/I]]} {error "TOUCH_I2C_NOT_OPEN_DRAIN $name"}
    audit_line "I2C_OPEN_DRAIN_HARD_WIRED=$name"
}
set synchronizers [get_cells -quiet -hier -regexp {.*touch_io/input_(meta|sync)_reg\[[0-3]\]}]
if {[llength $synchronizers] != 8} {error "TOUCH_SYNCHRONIZER_COUNT [llength $synchronizers]"}
foreach cell $synchronizers {if {![get_property ASYNC_REG $cell]} {error "TOUCH_SYNCHRONIZER_ATTRIBUTE_MISSING $cell"}}
audit_line "TOUCH_ASYNC_REG_COUNT=[llength $synchronizers]"
set pullup [get_property PULLUP [get_ports touch_int]]
if {$pullup ne {} && $pullup ni {0 FALSE false}} {error TOUCH_INT_MUST_NOT_HAVE_INTERNAL_PULLUP}
audit_line "TOUCH_INT_INTERNAL_PULLUP=$pullup"
audit_line TOUCH_IMPLEMENTATION_AUDIT_PASSED
close $channel
