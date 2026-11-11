# Host profile for every patched Endstone build (wheel and native test build).
#
# Endstone pins its direct Conan requirements, but some of their own
# requirements are version ranges that conancenter resolves to whatever is
# newest. When zlib/1.4 appeared there (2026-11), the same lock silently built
# against a different zlib and needed source builds of openssl, cpptrace,
# libdwarf and sentry-native, and openssl's Windows packaging then failed.
#
# Every range-resolved host requirement is pinned here, with its recipe
# revision, to what the 0.5.0 and 0.6.0 release candidates resolved for the
# locked pair. Change it only together with a compatibility pass, like
# endstone.lock. Entries a platform does not use are ignored there
# (libcurl, libunwind and xz_utils are Linux only).
include(default)

[replace_requires]
zlib/*: zlib/1.3.2#1cb806da49011867778ffb6ac7190fcb
zstd/*: zstd/1.5.7#b68ca8e3de04ba5957761751d1d661f4
libdwarf/*: libdwarf/2.1.0#eeec97d0fa36e5858f60706654ce50d8
xz_utils/*: xz_utils/5.8.4#8f8f25b0e4c422a4469a6f7ead5b355c
libunwind/*: libunwind/1.8.3#cc4113f21fee3389141f687e482113a0
libcurl/*: libcurl/8.23.0#5014d293481313f8a91f9bbdc2603f92
