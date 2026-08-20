
# Agent/Tools 的代办项

1. [ ] agent: 实现从md文件，或者toml文件加载的自定义Agent
2. [ ] agent: 对Agent模型增加ID,NAME等参数，支持从注册机(待实现)通过id,名称，(或者根据需求描述，通过embedded模型机制)动态匹配
3. [ ] tools: 实现/优化tools注册机机制，与agent一样，同样支持id, 名称，query 动态加载。
4. [ ] tools: 实现tools动态加载窗口，根据当前任务的需求，注册机动态返回可用tools的视图窗口。
5. [ ] tools: 实现常用的工具，git diff, glob, find等。。。
6. [ ] tools: 检查并优化现有的SHELL, PYTHON执行工具。