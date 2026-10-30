set tests_dir [file dirname [file normalize [info script]]]
set tools_dir [file normalize [file join $tests_dir .. tools]]
source [file normalize [file join $tools_dir .. build zynq_touch artifacts.tcl]]
set command_address $touch_command_address
set diagnostic_address $ui_mailbox_address
set base_values [lrepeat 32 0]
foreach {index value} {0 0x55545247 1 3 2 4 7 0x200 10 3 11 3072 12 1 13 2 24 256 25 23} {lset base_values $index $value}
foreach command {connect targets jtag fpga dow mrd mwr rst stop con disconnect exec after exit puts} {
    if {[llength [info commands $command]]} {rename $command offline_original_$command}
    proc $command {args} {error HARDWARE_ACCESS_FORBIDDEN_BY_OFFLINE_TEST}
}
proc puts {args} {lappend ::captured_output [join $args " "]}
proc after {milliseconds} {if {$milliseconds != 20} {error UNEXPECTED_POLL_INTERVAL}}
proc exit {status} {return -code error "OFFLINE_SCRIPT_EXIT=$status"}
proc exec {args} {
    if {[lindex $args 1] ne "-E" || [file tail [lindex $args 2]] ne "build_zynq.py" || [lrange $args 3 end] ne {--platform touch --verify-artifacts 2>@1}} {error UNEXPECTED_OFFLINE_EXEC}
    incr ::build_checks
    return OFFLINE_ARTIFACT_CHECK_FIXTURE
}
proc connect {args} {
    if {$args ne {-url tcp:127.0.0.1:3121}} {error UNEXPECTED_CONNECTION}
    incr ::connections
}
proc targets {args} {
    if {$args ne {-set -filter {name =~ "ARM*#0"}}} {error UNEXPECTED_TARGET}
}
proc disconnect {} {incr ::disconnections}
proc mwr {args} {
    if {[llength $args] != 3 || [lindex $args 0] ne "-force"} {error UNEXPECTED_MEMORY_WRITE}
    set address [lindex $args 1]
    set value [lindex $args 2]
    if {$address == $::command_address + 4 && $value > 0 && [llength $::writes] == 0} {
        set ::request_nonce $value
    } elseif {$address == $::command_address && $value == $::expected_command && [llength $::writes] == 1} {
        if {$::scenario ne "stale"} {set ::ack_nonce $::request_nonce}
        set ::ack_status [expr {$::scenario eq "rejected" ? 2 : 1}]
        lset ::diagnostics 27 [expr {$::scenario eq "wrong_last" ? 0 : $value}]
        if {$::scenario eq "pipeline_failed"} {lset ::diagnostics 8 0x10}
    } else {error UNEXPECTED_MEMORY_WRITE_ADDRESS_OR_ORDER}
    lappend ::writes [list $address $value]
}
proc mrd {args} {
    if {[llength $args] != 4 || [lrange $args 0 1] ne {-force -value}} {error UNEXPECTED_MEMORY_READ}
    set address [lindex $args 2]
    set count [lindex $args 3]
    if {$address == $::diagnostic_address && $count == 32} {return $::diagnostics}
    if {$address == $::command_address && $count == 1} {return [list [expr {$::scenario eq "busy" ? 1 : 0}]]}
    if {$address == $::command_address && $count == 4 && [llength $::writes] == 2} {
        return [list 0 $::request_nonce $::ack_nonce $::ack_status]
    }
    error UNEXPECTED_MEMORY_READ_ADDRESS_OR_ORDER
}
set cases 0
foreach scenario {success stale rejected wrong_last busy invalid_phase pipeline_failed} {
    foreach action [expr {$scenario eq "success" ? [list retry swap invert-x invert-y reset diagnostics] : [list retry]}] {
        set expected_command [expr {[lsearch -exact {retry swap invert-x invert-y reset diagnostics} $action] + 1}]
        set diagnostics $base_values
        if {$scenario eq "invalid_phase"} {lset diagnostics 2 3}
        set writes {}
        set connections 0
        set disconnections 0
        set build_checks 0
        set captured_output {}
        set ack_nonce 0
        set ack_status 0
        set argv [list $action]
        set result [catch {source [file join $tools_dir command_zynq_touch.tcl]} message]
        set expected_status [expr {$scenario eq "success" ? 0 : 1}]
        if {!$result || $message ne "OFFLINE_SCRIPT_EXIT=$expected_status"} {error "UNEXPECTED_SCRIPT_EXIT $scenario $action $message"}
        if {$connections != 1 || $disconnections != 1 || $build_checks != 1} {error CONNECTION_OR_ARTIFACT_CHECK_COUNT}
        set expected_writes [expr {$scenario in {busy invalid_phase} ? 0 : 2}]
        if {[llength $writes] != $expected_writes} {error MEMORY_WRITE_COUNT}
        if {$scenario eq "success" && ![string match {*TOUCH_UI_COMMAND_COMPLETE*} [join $captured_output "\n"]]} {error MISSING_SUCCESS_MARKER}
        incr cases
    }
}
offline_original_puts "TOUCH_MAILBOX_TCL_OFFLINE_PASSED=$cases / NO_HARDWARE_ACCESSED / CPU_CACHE_COHERENCE_STILL_REQUIRES_BOARD_TEST"
offline_original_exit 0
