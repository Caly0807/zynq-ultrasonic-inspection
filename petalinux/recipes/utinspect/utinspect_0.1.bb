SUMMARY = "Board-side ultrasound diagnosis, mapping and optional V4L2 vision"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = "file://board-service.tar.gz file://utinspect.init file://utinspect-launch file://utinspect.conf"
S = "${WORKDIR}"

inherit update-rc.d useradd

INITSCRIPT_NAME = "utinspect"
INITSCRIPT_PARAMS = "defaults 99"
USERADD_PACKAGES = "${PN}"
GROUPADD_PARAM:${PN} = "--system utinspect"
USERADD_PARAM:${PN} = "--system --gid utinspect --groups video --home-dir /var/lib/utinspect --no-create-home --shell /bin/false utinspect"
RDEPENDS:${PN} = "python3-core python3-modules"

do_install() {
    install -d ${D}${datadir}/utinspect ${D}${sysconfdir}/init.d ${D}${sysconfdir}/default ${D}${sbindir}
    cp -R ${WORKDIR}/utinspect ${WORKDIR}/web ${D}${datadir}/utinspect/
    install -m 0644 ${WORKDIR}/manifest.json ${D}${datadir}/utinspect/manifest.json
    install -m 0755 ${WORKDIR}/utinspect.init ${D}${sysconfdir}/init.d/utinspect
    install -m 0755 ${WORKDIR}/utinspect-launch ${D}${sbindir}/utinspect-launch
    install -m 0644 ${WORKDIR}/utinspect.conf ${D}${sysconfdir}/default/utinspect
    install -d -m 0700 ${D}${localstatedir}/lib/utinspect
    chown utinspect:utinspect ${D}${localstatedir}/lib/utinspect
}

FILES:${PN} += "${datadir}/utinspect ${localstatedir}/lib/utinspect"
CONFFILES:${PN} = "${sysconfdir}/default/utinspect"
