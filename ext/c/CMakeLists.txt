cmake_minimum_required(VERSION 3.16)
project(qxt_ext_c C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

# 公共 IPC 协议库 (被所有 C 子程序链接)
add_library(qxt_ipc STATIC
  common/ipc.c
)
target_include_directories(qxt_ipc PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/common)

# 各子程序的可执行文件
set(SUBPROGRAMS
  index
  diff
  crypto
  watch
  sandbox
  ansi
)

foreach(prog ${SUBPROGRAMS})
  add_executable(qxt_${prog} ${prog}/${prog}.c)
  target_link_libraries(qxt_${prog} qxt_ipc)
  # 输出统一到 dist/bin
  set_target_properties(qxt_${prog} PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY ${CMAKE_SOURCE_DIR}/../dist/bin)
endforeach()

# 平台相关源 (watch/sandbox 需要平台实现)
if(WIN32)
  target_sources(qxt_watch PRIVATE watch/watch_win.c)
  target_sources(qxt_sandbox PRIVATE sandbox/sandbox_win.c)
elseif(APPLE)
  target_sources(qxt_watch PRIVATE watch/watch_posix.c)
  target_sources(qxt_sandbox PRIVATE sandbox/sandbox_posix.c)
else()
  target_sources(qxt_watch PRIVATE watch/watch_posix.c)
  target_sources(qxt_sandbox PRIVATE sandbox/sandbox_posix.c)
endif()

# 测试 (C 侧自带 mini test: 每个程序支持 --selftest)
enable_testing()
foreach(prog ${SUBPROGRAMS})
  add_test(NAME selftest_${prog}
    COMMAND qxt_${prog} --selftest)
endforeach()
