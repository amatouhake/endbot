# Host profile for every patched Endstone build (wheel and native test build).
#
# Endstone pins its direct Conan requirements, but some of their own
# requirements are version ranges that conancenter resolves to whatever is
# newest. When zlib/1.4 appeared there (2026-11), the same lock silently built
# against a different zlib and needed source builds of openssl, cpptrace,
# libdwarf and sentry-native, and openssl's Windows packaging then failed.
# Pin what the locked pair was validated with; change it only together with a
# compatibility pass, like endstone.lock.
include(default)

[replace_requires]
zlib/*: zlib/1.3.2
