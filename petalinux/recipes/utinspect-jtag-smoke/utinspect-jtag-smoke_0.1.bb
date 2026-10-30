SUMMARY = "Explicit volatile, transmission-disabled JTAG network smoke service"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = "file://utinspect-jtag-smoke.init file://utinspect-smoke.token"
S = "${WORKDIR}"

inherit update-rc.d

INITSCRIPT_NAME = "utinspect-jtag-smoke"
INITSCRIPT_PARAMS = "defaults 99"
RDEPENDS:${PN} = "utinspect busybox"

do_install() {
    install -d ${D}${sysconfdir}/init.d ${D}${sysconfdir}/utinspect
    install -m 0755 ${WORKDIR}/utinspect-jtag-smoke.init ${D}${sysconfdir}/init.d/utinspect-jtag-smoke
    install -m 0600 ${WORKDIR}/utinspect-smoke.token ${D}${sysconfdir}/utinspect/jtag-smoke.token
}

FILES:${PN} += "${sysconfdir}/utinspect/jtag-smoke.token"
