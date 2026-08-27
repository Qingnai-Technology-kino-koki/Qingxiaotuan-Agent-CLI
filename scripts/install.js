#!/usr/bin/env node
/**
 * npm postinstall 脚本
 * 
 * 在 npm install 之后自动执行:
 * 1. 检测 Python 环境
 * 2. 安装 Python 依赖
 * 3. 运行引擎健康检查
 */

'use strict';

const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const PKG_ROOT = path.resolve(__dirname, '..');

function findPython() {
  const candidates = ['python3', 'python', 'py'];
  for (const cmd of candidates) {
    try {
      const version = execSync(`${cmd} --version`, { 
        encoding: 'utf-8', 
        timeout: 5000,
        stdio: ['pipe', 'pipe', 'pipe']
      });
      if (version.includes('Python 3.')) {
        return cmd;
      }
    } catch (e) {
      // 继续
    }
  }
  return null;
}

function main() {
  console.log('\n🐍 青小团 npm 包安装器\n');

  const python = findPython();
  if (!python) {
    console.warn('⚠️  未找到 Python 3, 跳过 Python 依赖安装');
    console.warn('   请手动安装: pip install -e .');
    console.warn('   下载 Python: https://www.python.org/downloads/\n');
    return;
  }

  console.log(`   Python: ${python}`);

  // 安装 Python 依赖
  console.log('📦 安装 Python 依赖...');
  try {
    execSync(`${python} -m pip install -e "${PKG_ROOT}" -q`, {
      stdio: 'inherit',
      timeout: 120000,
      cwd: PKG_ROOT,
    });
    console.log('✅ Python 依赖安装完成\n');
  } catch (e) {
    console.warn('⚠️  Python 依赖安装失败, 请手动运行: pip install -e .\n');
    return;
  }

  // 运行引擎健康检查
  console.log('🔍 引擎健康检查...');
  try {
    const result = execSync(`${python} -c "from qingxiaotuan.ext.registry import engine_healthcheck; import json; print(json.dumps(engine_healthcheck()))"`, {
      encoding: 'utf-8',
      timeout: 30000,
      cwd: PKG_ROOT,
    });
    const health = JSON.parse(result);
    const ok = Object.values(health).filter(v => v.ok).length;
    const total = Object.keys(health).length;
    console.log(`✅ ${ok}/${total} 引擎就绪\n`);
  } catch (e) {
    console.warn('⚠️  引擎健康检查跳过\n');
  }

  console.log('🎉 安装完成! 运行 `qxt chat` 开始使用\n');
}

main();
