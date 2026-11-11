include_directories(${OPENDNP3_INCLUDE_DIRS})
add_executable(mine-dnp3-poll main.cpp)
target_link_libraries(mine-dnp3-poll opendnp3)
install(TARGETS mine-dnp3-poll RUNTIME DESTINATION bin)
