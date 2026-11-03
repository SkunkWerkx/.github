# find_package(hypercast) for an unpacked release archive; ships as
# lib/cmake/hypercast/hypercastConfig.cmake. Every path is relative to this file, so the
# archive works wherever it is unpacked, and a target is declared only for a library the
# archive carries (the WebAssembly archive has no shared library).
#
#   hypercast::hypercast          the shared library
#   hypercast::hypercast_static   the static library, which needs nothing past the C runtime
#
# Both carry include/, so hypercast.h and the C++ header, hypercast.hpp, come with either.
#
# The version is the package's, from hypercastConfigVersion.cmake; at run time the library
# reports its own through hypercast_version().

get_filename_component(_hypercast_prefix "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)

if(WIN32)
  set(_hypercast_shared "${_hypercast_prefix}/bin/hypercast.dll")
  set(_hypercast_implib "${_hypercast_prefix}/lib/hypercast.dll.lib")
  set(_hypercast_static "${_hypercast_prefix}/lib/hypercast.lib")
elseif(APPLE)
  set(_hypercast_shared "${_hypercast_prefix}/lib/libhypercast.dylib")
  set(_hypercast_static "${_hypercast_prefix}/lib/libhypercast.a")
else()
  set(_hypercast_shared "${_hypercast_prefix}/lib/libhypercast.so")
  set(_hypercast_static "${_hypercast_prefix}/lib/libhypercast.a")
endif()

if(NOT TARGET hypercast::hypercast AND EXISTS "${_hypercast_shared}")
  add_library(hypercast::hypercast SHARED IMPORTED)
  set_target_properties(hypercast::hypercast PROPERTIES
    IMPORTED_LOCATION "${_hypercast_shared}"
    INTERFACE_INCLUDE_DIRECTORIES "${_hypercast_prefix}/include")
  if(WIN32)
    set_target_properties(hypercast::hypercast PROPERTIES IMPORTED_IMPLIB "${_hypercast_implib}")
  elseif(APPLE)
    set_target_properties(hypercast::hypercast PROPERTIES IMPORTED_SONAME "@rpath/libhypercast.dylib")
  else()
    set_target_properties(hypercast::hypercast PROPERTIES IMPORTED_SONAME "libhypercast.so")
  endif()
endif()

if(NOT TARGET hypercast::hypercast_static AND EXISTS "${_hypercast_static}")
  add_library(hypercast::hypercast_static STATIC IMPORTED)
  set_target_properties(hypercast::hypercast_static PROPERTIES
    IMPORTED_LOCATION "${_hypercast_static}"
    IMPORTED_LINK_INTERFACE_LANGUAGES C
    INTERFACE_INCLUDE_DIRECTORIES "${_hypercast_prefix}/include")
endif()

unset(_hypercast_prefix)
unset(_hypercast_shared)
unset(_hypercast_implib)
unset(_hypercast_static)
