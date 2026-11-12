// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <cstddef>
#include <string>
std::string encode(const char *bytes, size_t length);
std::string decode(const std::string &text);
bool read_line(std::string &result);
