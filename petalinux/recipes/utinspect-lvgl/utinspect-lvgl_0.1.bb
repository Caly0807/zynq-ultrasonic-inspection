SUMMARY = "Native ARM Linux LVGL ultrasound display and optional board camera startup"
LICENSE = "MIT & OFL-1.1"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302 file://vendor/lvgl/LICENCE.txt;md5=4570b6241b4fced1d1d18eb691a0e083 file://assets/noto_license.txt;md5=55719faa0112708e946b820b24b14097"
SRC_URI = "file://linux-ui.tar.gz file://utinspect-lvgl.init"
S = "${WORKDIR}/linux-ui"
inherit update-rc.d
INITSCRIPT_NAME = "utinspect-lvgl"
INITSCRIPT_PARAMS = "defaults 99"
DEPENDS = ""
RDEPENDS:${PN} = "utinspect ut-platform"

do_compile() {
    oe_runmake -f Makefile.linux OUT=build/linux
}

do_install() {
    install -d ${D}${bindir} ${D}${sysconfdir}/init.d ${D}${datadir}/licenses/utinspect-lvgl
    install -m 0755 build/linux/ut_lvgl_linux ${D}${bindir}/ut_lvgl_linux
    install -m 0755 build/linux/ut_uvc_capture ${D}${bindir}/ut_uvc_capture
    install -m 0755 ${WORKDIR}/utinspect-lvgl.init ${D}${sysconfdir}/init.d/utinspect-lvgl
    install -m 0644 vendor/lvgl/LICENCE.txt ${D}${datadir}/licenses/utinspect-lvgl/LVGL-MIT.txt
    install -m 0644 assets/noto_license.txt ${D}${datadir}/licenses/utinspect-lvgl/Noto-OFL-1.1.txt
}

FILES:${PN} += "${datadir}/licenses/utinspect-lvgl"
