# 启动器接入语言示例

Java、C、C++、C# 与 Python 复用同一 JSON-RPC 2.0 桥接口。一般以本机子进程启动 `buggcraft-bridge`；C/C++ 也可调用 C ABI。启动器不重写 SSH、P2P、DTLS/SCTP，也不直连节点内部协议。

| 语言 | 通信方式 | 常见组件 | 注意事项 |
|---|---|---|---|
| Java | `ProcessBuilder` 管道 | Jackson | UTF-8 读写；持续读取 stderr |
| C | `bridge.h` C ABI | C 标准库 | 响应由 `buggcraft_bridge_free` 释放 |
| C++ | `bridge.h` C ABI | `nlohmann/json` | RAII 管理桥和响应内存 |
| C# | `Process` 管道 | `System.Text.Json` | 异步读写，避免阻塞 UI |
| Python | `subprocess.Popen` 管道 | 标准库 `json` | 管理进程退出并持续读取 stderr |

## 接入顺序

1. 从可信发行流程取得原生桥及完整依赖目录。
2. 以子进程启动桥，stdin/stdout 使用 UTF-8；stdout 专用于协议，stderr 需持续消费。
3. 调用 `bridge.initialize`，检查运行时 `protocol`、`methods` 和 `limits`。
4. 调用 `node.add` 导入用户从可信节点取得的接入码；凭据不得写入命令行、日志或 URL。
5. 用 `node` 句柄创建/加入房间，将返回的回环 `address` 交给 Minecraft；结束时调用 `room.close`。
6. 轮询 `session.events`、`session.list` 与 `bridge.events`。关闭 stdin 会清理会话。

每行一个 JSON-RPC 2.0 请求，不支持 batch。错误由 `error` 对象返回。参见[桥接协议](launcher-bridge.md)及[恢复边界](compatibility.md)。

## Python 子进程示例

生产桌面应用应在工作线程调用 RPC，并为 stderr 安排持续读取器。

```python
import json
import subprocess

bridge = subprocess.Popen(
    [bridge_path], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1
)

def rpc(request_id, method, params):
    request = {"jsonrpc": "2.0", "id": request_id,
               "method": method, "params": params}
    bridge.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
    bridge.stdin.flush()
    reply = json.loads(bridge.stdout.readline())
    if "error" in reply:
        raise RuntimeError(reply["error"].get("message", "Bridge error"))
    return reply["result"]

rpc(1, "bridge.initialize", {"protocol": 1})
node = rpc(2, "node.add", {"code": access_code})["node"]
room = rpc(3, "room.join", {"node": node, "invite": invite,
                             "transport": "auto"})
launch_minecraft(room["address"])
rpc(4, "room.close", {"session": room["session"]})
bridge.stdin.close()
bridge.wait(timeout=5)
```

`bridge_path`、`access_code`、`invite` 和 `launch_minecraft` 由启动器提供。代码片段省略 stderr 读取器，不应直接用于长时间运行的产品进程。

## C ABI 初始化示例

```c
#include <buggcraft/bridge.h>
#include <stdio.h>
#include <string.h>

int main(void) {
  buggcraft_bridge *bridge = NULL;
  const char request[] = "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"bridge.initialize\",\"params\":{\"protocol\":1}}";
  char *response = NULL; size_t size = 0;
  if (buggcraft_bridge_abi_version() != 1 || buggcraft_bridge_create(&bridge)) return 1;
  int status = buggcraft_bridge_call(bridge, request, strlen(request), &response, &size);
  if (!status && response) fwrite(response, 1, size, stdout);
  buggcraft_bridge_free(response);
  buggcraft_bridge_destroy(bridge);
  return status != 0;
}
```

## C++

```cpp
#include <buggcraft/bridge.h>
#include <nlohmann/json.hpp>
#include <iostream>
#include <stdexcept>

using json = nlohmann::json;
int main() {
  buggcraft_bridge *bridge = nullptr;
  if (buggcraft_bridge_abi_version() != 1 || buggcraft_bridge_create(&bridge))
    throw std::runtime_error("bridge initialization failed");
  const auto request = json{{"jsonrpc", "2.0"}, {"id", 1},
    {"method", "bridge.initialize"}, {"params", {{"protocol", 1}}}}.dump();
  char *response = nullptr; size_t size = 0;
  int status = buggcraft_bridge_call(bridge, request.data(), request.size(), &response, &size);
  if (!status && response) std::cout.write(response, static_cast<std::streamsize>(size));
  buggcraft_bridge_free(response);
  buggcraft_bridge_destroy(bridge);
  return status != 0;
}
```

## Java

Java 17+ 与 Jackson 示例，`bridgePath`、`accessCode`、`invite` 和游戏启动函数由调用方提供。正式应用另为 stderr 建立持续读取任务，并管理管道超时。

```java
var process = new ProcessBuilder(bridgePath).start();
var in = new java.io.BufferedWriter(new java.io.OutputStreamWriter(
    process.getOutputStream(), java.nio.charset.StandardCharsets.UTF_8));
var out = new java.io.BufferedReader(new java.io.InputStreamReader(
    process.getInputStream(), java.nio.charset.StandardCharsets.UTF_8));
var json = new com.fasterxml.jackson.databind.ObjectMapper();
var sequence = new java.util.concurrent.atomic.AtomicInteger();
java.util.function.BiFunction<String, java.util.Map<String,Object>, com.fasterxml.jackson.databind.JsonNode> rpc = (method, params) -> {
  try {
    var request = java.util.Map.of("jsonrpc", "2.0", "id", sequence.incrementAndGet(),
                                  "method", method, "params", params);
    in.write(json.writeValueAsString(request)); in.newLine(); in.flush();
    var reply = json.readTree(out.readLine());
    if (reply.has("error")) throw new RuntimeException(reply.get("error").toString());
    return reply.get("result");
  } catch (java.io.IOException e) { throw new RuntimeException(e); }
};
rpc.apply("bridge.initialize", java.util.Map.of("protocol", 1));
var node = rpc.apply("node.add", java.util.Map.of("code", accessCode)).get("node").asText();
var room = rpc.apply("room.join", java.util.Map.of("node", node, "invite", invite, "transport", "auto"));
launchMinecraft(room.get("address").asText());
rpc.apply("room.close", java.util.Map.of("session", room.get("session").asText()));
```

## C#

以下使用 .NET 6+ 的进程管道和 `System.Text.Json`。正式应用必须持续读取 `StandardError`，并设定退出和 RPC 超时。

```csharp
var start = new System.Diagnostics.ProcessStartInfo(bridgePath) {
    RedirectStandardInput = true, RedirectStandardOutput = true,
    RedirectStandardError = true, UseShellExecute = false, CreateNoWindow = true
};
using var bridge = System.Diagnostics.Process.Start(start)!;
async System.Threading.Tasks.Task<System.Text.Json.JsonElement> Rpc(
    int id, string method, object parameters) {
  var request = System.Text.Json.JsonSerializer.Serialize(new {
      jsonrpc = "2.0", id, method, @params = parameters });
  await bridge.StandardInput.WriteLineAsync(request);
  await bridge.StandardInput.FlushAsync();
  using var doc = System.Text.Json.JsonDocument.Parse(
      (await bridge.StandardOutput.ReadLineAsync())!);
  var root = doc.RootElement;
  if (root.TryGetProperty("error", out var error))
      throw new System.Exception(error.ToString());
  return root.GetProperty("result").Clone();
}
await Rpc(1, "bridge.initialize", new { protocol = 1 });
var node = (await Rpc(2, "node.add", new { code = accessCode }))
    .GetProperty("node").GetString();
var room = await Rpc(3, "room.join", new { node, invite, transport = "auto" });
LaunchMinecraft(room.GetProperty("address").GetString());
await Rpc(4, "room.close", new {
    session = room.GetProperty("session").GetString() });
```

每个请求应有独立 ID。正式应用还应校验响应 ID、处理 EOF 与错误 JSON，并持续消费 stderr。完整线程、内存和运行目录约束见[原生桥动态库接口](../bridge-library.md)。

游戏版本、加载器、模组分页和下载参数见[游戏环境与模组同步](game-metadata.md)；账号 API 见[网页授权接口](web-api.md)。
