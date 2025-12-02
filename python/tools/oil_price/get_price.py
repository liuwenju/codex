import stealth_requests as requests
from lxml import html, etree
from datetime import datetime
import re
import os
import json
import time
from typing import Dict, Optional, Tuple, List
from dataclasses import dataclass
import logging

# ==================== 配置区域 ====================
# 建议将敏感信息存储在环境变量中
SERVERCHAN_SENDKEY = os.getenv("SERVERCHAN_SENDKEY", "YOUR_SENDKEY_HERE")  # 从环境变量读取
OIL_PRICE_URL = "http://m.qiyoujiage.com/zhejiang.shtml"
# 备用数据源（如果主源失败可尝试）
BACKUP_SOURCES = [
    "https://datapc.eastmoney.com/soft/cjsj/yjtz/zhejiang.html",  # 东方财富网[citation:6]
]
SERVERCHAN_API = "https://sctapi.ftqq.com/{sendkey}.send"

# 数据提取配置 - XPath表达式
XPATH_CONFIG = {
    "price_div": "/html/body/div[5]/div[2]/div[1]",
    "adjustment_div": "/html/body/div[5]/div[2]/div[2]",
    # 备用选择器（应对网站结构变化）
    "backup_selectors": [
        "//div[contains(@class, 'price')]",
        "//div[contains(text(), '92号汽油') or contains(text(), '95号汽油')]"
    ]
}

# 设置日志记录
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('oil_price_monitor.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ==================== 数据类定义 ====================
@dataclass
class OilPriceData:
    """油价数据容器类"""
    timestamp: str
    prices: Dict[str, str]  # {油品类型: 价格}
    adjustment_info: str
    source: str
    success: bool
    message: str = ""

# ==================== 核心函数 ====================

def fetch_with_retry(url: str, max_retries: int = 3, timeout: int = 15) -> Optional[requests.response]:
    """
    带重试机制的请求函数
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Referer': 'https://www.baidu.com/',
    }
    
    for attempt in range(max_retries):
        try:
            logger.info(f"尝试请求 {url} (第 {attempt + 1} 次)")
            # 使用StealthSession保持会话[citation:5][citation:10]
            from stealth_requests import StealthSession
            with StealthSession() as session:
                response = session.get(url, headers=headers, timeout=timeout)
                response.raise_for_status()
                
            # 自动检测编码
            if response.encoding is None or response.encoding.lower() not in ['utf-8', 'gbk', 'gb2312']:
                response.encoding = 'utf-8'
                
            logger.info(f"请求成功: 状态码 {response.status_code}")
            return response
            
        except requests.exceptions.Timeout:
            logger.warning(f"请求超时 (尝试 {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # 指数退避
        except requests.exceptions.RequestException as e:
            logger.error(f"请求失败: {e}")
            if attempt < max_retries - 1:
                time.sleep(1)
        except Exception as e:
            logger.error(f"未知错误: {e}")
            break
    
    return None

def extract_prices_advanced(html_content: str, url: str) -> Dict[str, str]:
    """
    高级油价提取函数，支持多种解析策略[citation:9]
    """
    prices = {}
    
    try:
        tree = html.fromstring(html_content)
        
        # 策略1: 使用原始XPath
        price_div = tree.xpath(XPATH_CONFIG["price_div"])
        if price_div:
            price_text = price_div[0].text_content().strip()
            extracted = extract_specific_oil_prices(price_text)
            if extracted:
                prices.update(extracted)
                logger.info("通过主XPath提取油价成功")
        
        # 策略2: 如果主策略失败，尝试备用选择器
        if not prices:
            for selector in XPATH_CONFIG["backup_selectors"]:
                elements = tree.xpath(selector)
                for elem in elements[:3]:  # 检查前3个元素
                    text = elem.text_content().strip()
                    extracted = extract_specific_oil_prices(text)
                    if extracted:
                        prices.update(extracted)
                        logger.info(f"通过备用选择器 '{selector}' 提取油价成功")
                        break
                if prices:
                    break
        
        # 策略3: 尝试正则表达式全局搜索（作为最后手段）
        if not prices:
            patterns = {
                '92号汽油': r'92号汽油[^\d]*([\d\.]+)\s*元',
                '95号汽油': r'95号汽油[^\d]*([\d\.]+)\s*元',
                '汽油92': r'汽油92[^\d]*([\d\.]+)\s*元',
                '汽油95': r'汽油95[^\d]*([\d\.]+)\s*元'
            }
            
            for oil_type, pattern in patterns.items():
                match = re.search(pattern, html_content)
                if match:
                    price = match.group(1)
                    key = '92号汽油' if '92' in oil_type else '95号汽油'
                    prices[key] = price
                    logger.info(f"通过正则表达式提取 {key} 价格: {price}")
        
        # 验证提取结果
        for oil_type in ['92号汽油', '95号汽油']:
            if oil_type in prices:
                # 价格合理性检查（通常油价在5-10元之间）
                try:
                    price_val = float(prices[oil_type])
                    if price_val < 5 or price_val > 10:
                        logger.warning(f"{oil_type} 价格 {price_val} 元可能异常")
                except ValueError:
                    logger.warning(f"{oil_type} 价格格式异常: {prices[oil_type]}")
    
    except Exception as e:
        logger.error(f"解析HTML内容时出错: {e}")
    
    return prices

def extract_adjustment_info(html_content: str) -> str:
    """
    提取下次调整信息，增强清理功能
    """
    try:
        tree = html.fromstring(html_content)
        
        # 尝试多个可能的选择器
        adjustment_selectors = [
            "/html/body/div[5]/div[2]/div[2]",
            "//div[contains(text(), '调整') or contains(text(), '调价')]",
            "//div[@class='adjustment' or contains(@id, 'adjust')]"
        ]
        
        adjustment_text = ""
        for selector in adjustment_selectors:
            elements = tree.xpath(selector)
            if elements:
                adjustment_text = elements[0].text_content().strip()
                if adjustment_text and len(adjustment_text) > 10:  # 有效内容检查
                    break
        
        if adjustment_text:
            # 清理JavaScript和其他噪音[citation:9]
            lines = adjustment_text.split('\n')
            clean_lines = []
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                    
                # 过滤JavaScript代码
                if any(js_keyword in line for js_keyword in 
                      ['var ', 'function', 'document.', 'alert(', 'console.', 'getElement']):
                    continue
                    
                # 保留包含中文或重要关键词的行
                if re.search(r'[\u4e00-\u9fff]|调整|调价|油价|时间|预计', line):
                    # 移除多余空格和特殊字符
                    line = re.sub(r'\s+', ' ', line)
                    line = re.sub(r'[\[\]{}()<>]', '', line)
                    clean_lines.append(line)
            
            # 取最重要的行（通常前2-3行）
            result = ' '.join(clean_lines[:3])
            if result:
                return result[:150]  # 限制长度
        
        return "暂无下次调整信息或信息解析失败"
        
    except Exception as e:
        logger.error(f"提取调整信息时出错: {e}")
        return "调整信息提取失败"

def fetch_oil_price_from_source(url: str, source_name: str = "主数据源") -> OilPriceData:
    """
    从指定数据源获取油价信息
    """
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    try:
        response = fetch_with_retry(url)
        if not response:
            return OilPriceData(
                timestamp=timestamp,
                prices={},
                adjustment_info="",
                source=source_name,
                success=False,
                message=f"无法从{source_name}获取数据"
            )
        
        # 提取油价
        prices = extract_prices_advanced(response.content.decode(response.encoding, errors='ignore'), url)
        
        # 提取调整信息
        adjustment_info = extract_adjustment_info(response.content)
        
        return OilPriceData(
            timestamp=timestamp,
            prices=prices,
            adjustment_info=adjustment_info,
            source=source_name,
            success=len(prices) > 0,
            message="数据获取成功" if prices else "未找到油价数据"
        )
        
    except Exception as e:
        logger.error(f"从{source_name}获取油价时出错: {e}")
        return OilPriceData(
            timestamp=timestamp,
            prices={},
            adjustment_info="",
            source=source_name,
            success=False,
            message=str(e)
        )

def fetch_oil_price_with_fallback() -> OilPriceData:
    """
    获取油价信息，支持备用数据源[citation:7]
    """
    # 尝试主数据源
    main_data = fetch_oil_price_from_source(OIL_PRICE_URL, "主数据源")
    
    # 如果主数据源失败，尝试备用源
    if not main_data.success or len(main_data.prices) < 2:
        logger.warning("主数据源获取失败或数据不全，尝试备用源...")
        for i, backup_url in enumerate(BACKUP_SOURCES, 1):
            backup_data = fetch_oil_price_from_source(backup_url, f"备用源{i}")
            if backup_data.success and len(backup_data.prices) >= 1:
                logger.info(f"从备用源{i}获取数据成功")
                return backup_data
    
    return main_data

def format_oil_price_message(data: OilPriceData) -> Tuple[str, str]:
    """
    格式化油价信息为推送消息[citation:3]
    
    返回: (标题, 详细内容)
    """
    # 基础标题
    if data.success and data.prices:
        price_types = list(data.prices.keys())
        title = f"浙江油价更新: {', '.join(price_types)}"
    else:
        title = "油价获取通知"
    
    # 详细内容 (Markdown格式)
    desp_lines = []
    
    desp_lines.append(f"## ⛽ 浙江最新油价信息")
    desp_lines.append(f"**抓取时间:** {data.timestamp}")
    desp_lines.append(f"**数据来源:** {data.source}")
    desp_lines.append("")
    
    if data.prices:
        desp_lines.append("### 当前油价")
        for oil_type, price in data.prices.items():
            desp_lines.append(f"- **{oil_type}:** `{price} 元/升`")
    else:
        desp_lines.append("### ❌ 油价获取失败")
        desp_lines.append(f"错误信息: {data.message}")
    
    desp_lines.append("")
    
    if data.adjustment_info:
        desp_lines.append("### 📅 下次调整提醒")
        desp_lines.append(f"{data.adjustment_info}")
    
    desp_lines.append("")
    desp_lines.append("---")
    desp_lines.append("*数据仅供参考，实际油价以加油站为准*")
    
    return title, "\n".join(desp_lines)

def send_to_serverchan(title: str, desp: str, sendkey: str = None) -> bool:
    """
    通过ServerChan发送消息到微信[citation:3][citation:8]
    
    返回: 是否成功
    """
    if sendkey is None:
        sendkey = SERVERCHAN_SENDKEY
    
    if sendkey == "YOUR_SENDKEY_HERE":
        logger.error("请设置ServerChan SendKey！")
        logger.info("请到 https://sct.ftqq.com/ 注册获取SendKey")
        return False
    
    api_url = SERVERCHAN_API.format(sendkey=sendkey)
    
    try:
        data = {
            "text": title[:100],  # 标题限制长度
            "desp": desp
        }
        
        logger.info("正在发送消息到ServerChan...")
        response = requests.post(api_url, data=data, timeout=10)
        
        if response.status_code == 200:
            result = response.json()
            if result.get("code") == 0 or "success" in response.text.lower():
                logger.info("ServerChan消息发送成功")
                return True
            else:
                logger.error(f"ServerChan返回错误: {result}")
                return False
        else:
            logger.error(f"ServerChan请求失败: 状态码 {response.status_code}")
            return False
            
    except requests.exceptions.RequestException as e:
        logger.error(f"发送ServerChan请求时出错: {e}")
        return False
    except Exception as e:
        logger.error(f"处理ServerChan推送时出错: {e}")
        return False

def extract_specific_oil_prices(text: str) -> Dict[str, str]:
    """
    精确提取92号和95号汽油价格（原函数优化版）
    """
    prices = {}
    
    # 改进的正则表达式模式
    patterns = {
        '92号汽油': [
            r'92号汽油[^\d]*([\d\.]+)\s*[元\(]',
            r'92[^\d]*([\d\.]+)\s*元',
            r'汽油92[^\d]*([\d\.]+)'
        ],
        '95号汽油': [
            r'95号汽油[^\d]*([\d\.]+)\s*[元\(]',
            r'95[^\d]*([\d\.]+)\s*元',
            r'汽油95[^\d]*([\d\.]+)'
        ]
    }
    
    for oil_type, pattern_list in patterns.items():
        for pattern in pattern_list:
            match = re.search(pattern, text)
            if match:
                price = match.group(1)
                # 价格验证
                if re.match(r'^\d+\.?\d*$', price):
                    prices[oil_type] = price
                    break
    
    return prices

# ==================== 主函数 ====================

def main():
    """
    主函数：获取油价并推送到微信[citation:8]
    """
    logger.info("=" * 60)
    logger.info("开始抓取浙江油价信息...")
    logger.info("=" * 60)
    
    # 1. 获取油价数据
    oil_data = fetch_oil_price_with_fallback()
    
    # 2. 格式化消息
    title, message = format_oil_price_message(oil_data)
    
    # 3. 控制台输出
    print("\n" + "=" * 60)
    print(f"抓取时间: {oil_data.timestamp}")
    print(f"数据来源: {oil_data.source}")
    print(f"状态: {'成功' if oil_data.success else '失败'}")
    print("-" * 60)
    
    if oil_data.prices:
        print("浙江最新油价:")
        for oil_type, price in oil_data.prices.items():
            print(f"  {oil_type}: {price}元/升")
    else:
        print(f"错误: {oil_data.message}")
    
    if oil_data.adjustment_info:
        print("-" * 60)
        print(f"下次油价调整提醒:\n{oil_data.adjustment_info}")
    
    print("=" * 60)
    
    # 4. 推送到微信（仅在成功获取油价或需要通知失败时推送）
    if oil_data.success or ("失败" in oil_data.message):
        push_success = send_to_serverchan(title, message)
        
        if push_success:
            print("✅ 油价信息已推送到微信")
        else:
            print("❌ 微信推送失败，请检查ServerChan配置")
    else:
        print("⚠️  数据获取失败，未执行微信推送")
    
    logger.info("程序执行完成")

if __name__ == "__main__":
    # 配置检查
    if SERVERCHAN_SENDKEY == "YOUR_SENDKEY_HERE":
        print("⚠️  警告: 请先配置ServerChan SendKey")
        print("1. 访问 https://sct.ftqq.com/ 注册并获取SendKey")
        print("2. 将SendKey设置为环境变量 SERVERCHAN_SENDKEY")
        print("   或直接修改代码中的 SERVERCHAN_SENDKEY 变量")
        print("-" * 60)
    
    main()
