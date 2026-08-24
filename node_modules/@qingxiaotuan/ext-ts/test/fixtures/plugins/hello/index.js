// 测试插件: 实现 greet 与 add 两个方法
module.exports = {
  async greet(params) {
    return `hello, ${params?.name || "world"}!`;
  },
  async add(params) {
    return Number(params?.a || 0) + Number(params?.b || 0);
  },
};
