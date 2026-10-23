// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <functional>
#include <string>
#include <utility>
#include <vector>
void add_journal_tests(std::vector<std::pair<std::string, std::function<void()>>>& cases);
