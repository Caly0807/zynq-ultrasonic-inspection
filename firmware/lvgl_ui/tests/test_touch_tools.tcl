set tests_dir [file dirname [file normalize [info script]]]
set tools_dir [file normalize [file join $tests_dir .. tools]]
source [file join $tools_dir touch_diagnostics.tcl]
set values [lrepeat 32 0]
foreach {index value} {0 0x55545247 1 3 2 4 7 0x200 10 3 11 3072 12 1 13 2 24 256 25 23} {lset values $index $value}
assert_touch_running $values
foreach {index value} {0 0 1 2 2 3 3 1 7 0x2c0 8 0x10 9 1 10 4 11 3071 12 0 13 1 24 255 25 22} {
    set invalid $values
    lset invalid $index $value
    if {![catch {assert_touch_running $invalid}]} {error "DIAGNOSTIC_GUARD_DID_NOT_REJECT_INDEX_$index"}
}
if {![catch {assert_touch_running [lrange $values 0 30]}]} {error SHORT_MAILBOX_NOT_REJECTED}
set identified $values
lset identified 16 3
print_touch_diagnostics $identified
lset identified 16 4
if {![catch {print_touch_diagnostics $identified} message] || $message ne "UNKNOWN_TOUCH_DRIVER_KIND"} {error UNKNOWN_TOUCH_KIND_NOT_REJECTED}
set hardware_calls 0
foreach command {connect targets jtag fpga dow mrd mwr rst stop con} {
    if {[llength [info commands $command]]} {rename $command original_$command}
    proc $command {args} {incr ::hardware_calls; error HARDWARE_ACCESS_FORBIDDEN_BY_OFFLINE_TEST}
}
set argv {}
set result [catch {source [file join $tools_dir load_zynq_touch.tcl]} message]
if {!$result || ![string match "Physical HV OFF confirmation*" $message]} {error "HV_GUARD_FAILED $message"}
set result [catch {source [file join $tools_dir command_zynq_touch.tcl]} message]
if {!$result || ![string match "Usage: command_zynq_touch*" $message]} {error "COMMAND_ARGUMENT_GUARD_FAILED $message"}
set argv {invalid arguments}
set result [catch {source [file join $tools_dir inspect_zynq_touch.tcl]} message]
if {!$result || ![string match "Usage: inspect_zynq_touch*" $message]} {error "INSPECTION_ARGUMENT_GUARD_FAILED $message"}
if {$hardware_calls != 0} {error UNEXPECTED_HARDWARE_ACCESS}
puts TOUCH_TCL_OFFLINE_GUARDS_PASSED_NO_HARDWARE_ACCESSED
exit 0
