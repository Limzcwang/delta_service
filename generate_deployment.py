#!/usr/bin/env python3
"""
Delta Service 部署配置生成器

根据环境配置自动生成OpenCloud部署文件
支持开发、测试、生产环境的差异化配置
"""

import yaml
import os
import sys
from datetime import datetime
from pathlib import Path


def load_config(config_file="config.yaml"):
    """加载配置文件"""
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except Exception as e:
        print(f"错误: 无法加载配置文件 {config_file}: {e}")
        sys.exit(1)


def validate_environment(config, environment):
    """验证环境配置"""
    if environment not in ['development', 'test', 'production']:
        print(f"错误: 不支持的环境 '{environment}'，请使用 development, test 或 production")
        sys.exit(1)
    
    if environment not in config:
        print(f"错误: 配置文件中未找到环境 '{environment}' 的配置")
        sys.exit(1)


def generate_deployment_yaml(config, environment, output_file=None):
    """生成OpenCloud部署YAML文件"""
    
    env_config = config[environment]
    base_config = config['base']
    
    # 生成部署配置
    deployment_yaml = {
        'apiVersion': 'apps/v1',
        'kind': 'Deployment',
        'metadata': {
            'name': f"delta-service-{environment}",
            'namespace': config['namespaces'][environment],
            'labels': {
                'app': 'delta-service',
                'environment': environment,
                'version': base_config['version']
            }
        },
        'spec': {
            'replicas': env_config['deployment']['replicas'],
            'selector': {
                'matchLabels': {
                    'app': 'delta-service',
                    'environment': environment
                }
            },
            'strategy': {
                'type': config['deployment_strategies'][environment]['type'],
                'rollingUpdate': config['deployment_strategies'][environment]['rolling_update']
            },
            'template': {
                'metadata': {
                    'labels': {
                        'app': 'delta-service',
                        'environment': environment,
                        'version': base_config['version']
                    }
                },
                'spec': {
                    'containers': [{
                        'name': 'delta-service',
                        'image': f"delta-service:{base_config['version']}",
                        'ports': [
                            {
                                'name': 'http',
                                'containerPort': base_config['service']['http_port'],
                                'protocol': 'TCP'
                            },
                            {
                                'name': 'websocket',
                                'containerPort': base_config['service']['websocket_port'],
                                'protocol': 'TCP'
                            }
                        ],
                        'resources': env_config['resources'],
                        'env': generate_environment_variables(config, environment),
                        'livenessProbe': generate_probe_config(config['health_checks']['liveness']),
                        'readinessProbe': generate_probe_config(config['health_checks']['readiness']),
                        'volumeMounts': [
                            {
                                'name': 'logs-volume',
                                'mountPath': '/app/logs'
                            }
                        ]
                    }],
                    'volumes': [
                        {
                            'name': 'logs-volume',
                            'persistentVolumeClaim': {
                                'claimName': f'delta-service-logs-{environment}'
                            }
                        }
                    ]
                }
            }
        }
    }
    
    # 如果启用了自动扩缩容
    if env_config['deployment']['autoscaling']['enabled']:
        hpa_yaml = {
            'apiVersion': 'autoscaling/v2',
            'kind': 'HorizontalPodAutoscaler',
            'metadata': {
                'name': f'delta-service-hpa-{environment}',
                'namespace': config['namespaces'][environment]
            },
            'spec': {
                'scaleTargetRef': {
                    'apiVersion': 'apps/v1',
                    'kind': 'Deployment',
                    'name': f'delta-service-{environment}'
                },
                'minReplicas': env_config['deployment']['autoscaling']['min_replicas'],
                'maxReplicas': env_config['deployment']['autoscaling']['max_replicas'],
                'metrics': [
                    {
                        'type': 'Resource',
                        'resource': {
                            'name': 'cpu',
                            'target': {
                                'type': 'Utilization',
                                'averageUtilization': env_config['deployment']['autoscaling']['target_cpu']
                            }
                        }
                    }
                ]
            }
        }
    else:
        hpa_yaml = None
    
    # 生成服务配置
    service_yaml = {
        'apiVersion': 'v1',
        'kind': 'Service',
        'metadata': {
            'name': f'delta-service-{environment}',
            'namespace': config['namespaces'][environment],
            'labels': {
                'app': 'delta-service',
                'environment': environment
            }
        },
        'spec': {
            'selector': {
                'app': 'delta-service',
                'environment': environment
            },
            'ports': [
                {
                    'name': 'http',
                    'port': 80,
                    'targetPort': base_config['service']['http_port'],
                    'protocol': 'TCP'
                },
                {
                    'name': 'websocket',
                    'port': base_config['service']['websocket_port'],
                    'targetPort': base_config['service']['websocket_port'],
                    'protocol': 'TCP'
                }
            ],
            'type': env_config['network']['service_type']
        }
    }
    
    # 生成存储配置
    pvc_yaml = {
        'apiVersion': 'v1',
        'kind': 'PersistentVolumeClaim',
        'metadata': {
            'name': f'delta-service-logs-{environment}',
            'namespace': config['namespaces'][environment]
        },
        'spec': {
            'accessModes': ['ReadWriteOnce'],
            'resources': {
                'requests': {
                    'storage': env_config['storage']['logs']['size']
                }
            },
            'storageClassName': env_config['storage']['logs']['class']
        }
    }
    
    # 如果启用了网络策略
    if env_config.get('security', {}).get('network_policy', {}).get('enabled', False):
        network_policy_yaml = {
            'apiVersion': 'networking.k8s.io/v1',
            'kind': 'NetworkPolicy',
            'metadata': {
                'name': f'delta-service-np-{environment}',
                'namespace': config['namespaces'][environment]
            },
            'spec': {
                'podSelector': {
                    'matchLabels': {
                        'app': 'delta-service'
                    }
                },
                'ingress': [
                    {
                        'from': [
                            {
                                'ipBlock': {
                                    'cidr': cidr
                                }
                            } for cidr in env_config['security']['network_policy']['allowed_cidrs']
                        ],
                        'ports': [
                            {
                                'protocol': 'TCP',
                                'port': base_config['service']['http_port']
                            },
                            {
                                'protocol': 'TCP',
                                'port': base_config['service']['websocket_port']
                            }
                        ]
                    }
                ]
            }
        }
    else:
        network_policy_yaml = None
    
    # 组合所有配置
    all_yamls = [deployment_yaml, service_yaml, pvc_yaml]
    if hpa_yaml:
        all_yamls.append(hpa_yaml)
    if network_policy_yaml:
        all_yamls.append(network_policy_yaml)
    
    # 生成YAML文件
    yaml_content = "# Delta Service OpenCloud 部署配置\n"
    yaml_content += f"# 环境: {environment}\n"
    yaml_content += f"# 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    yaml_content += "# 注意: 此文件由脚本自动生成，请勿手动修改\n\n"
    
    for yaml_obj in all_yamls:
        yaml_content += "---\n"
        yaml_content += yaml.dump(yaml_obj, default_flow_style=False, indent=2)
    
    # 输出到文件或控制台
    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(yaml_content)
        print(f"✓ 部署配置已生成: {output_file}")
    else:
        print(yaml_content)
    
    return yaml_content


def generate_environment_variables(config, environment):
    """生成环境变量配置"""
    env_vars = []
    
    # 添加通用环境变量
    for key, value in config['env_vars']['common'].items():
        env_vars.append({
            'name': key,
            'value': str(value)
        })
    
    # 添加环境特定变量
    if environment in config['env_vars']:
        for key, value in config['env_vars'][environment].items():
            env_vars.append({
                'name': key,
                'value': str(value)
            })
    
    # 添加环境标识
    env_vars.append({
        'name': 'ENVIRONMENT',
        'value': environment
    })
    
    return env_vars


def generate_probe_config(probe_config):
    """生成健康检查配置"""
    return {
        'httpGet': {
            'path': probe_config['path'],
            'port': probe_config['port']
        },
        'initialDelaySeconds': probe_config['initial_delay'],
        'periodSeconds': probe_config['period'],
        'timeoutSeconds': probe_config['timeout'],
        'successThreshold': probe_config['success_threshold'],
        'failureThreshold': probe_config['failure_threshold']
    }


def generate_dockerfile(config, environment, output_file="Dockerfile"):
    """生成Dockerfile"""
    
    dockerfile_content = f"""# Delta Service Dockerfile
# 环境: {environment}
# 版本: {config['base']['version']}

FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 复制依赖文件
COPY requirements.txt .

# 安装依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

# 创建日志目录
RUN mkdir -p logs

# 暴露端口
EXPOSE {config['base']['service']['http_port']} {config['base']['service']['websocket_port']}

# 设置环境变量
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV ENVIRONMENT={environment}

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \\
    CMD curl -f http://localhost:{config['base']['service']['http_port']}/docs || exit 1

# 启动命令
CMD ["python", "main.py"]
"""
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(dockerfile_content)
    
    print(f"✓ Dockerfile已生成: {output_file}")


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Delta Service 部署配置生成器')
    parser.add_argument('environment', choices=['development', 'test', 'production'], 
                       help='目标环境')
    parser.add_argument('-o', '--output', help='输出文件路径')
    parser.add_argument('-c', '--config', default='config.yaml', help='配置文件路径')
    parser.add_argument('--dockerfile', action='store_true', help='生成Dockerfile')
    
    args = parser.parse_args()
    
    # 加载配置
    config = load_config(args.config)
    
    # 验证环境
    validate_environment(config, args.environment)
    
    # 生成输出文件名
    if not args.output:
        args.output = f"opencloud-{args.environment}.yaml"
    
    # 生成部署配置
    generate_deployment_yaml(config, args.environment, args.output)
    
    # 如果需要生成Dockerfile
    if args.dockerfile:
        generate_dockerfile(config, args.environment)
    
    print(f"\n🎉 {args.environment}环境部署配置生成完成！")
    print(f"📁 配置文件: {args.output}")
    print(f"🌍 目标环境: {args.environment}")
    print(f"📦 命名空间: {config['namespaces'][args.environment]}")
    print(f"🔧 副本数: {config[args.environment]['deployment']['replicas']}")
    
    # 显示部署命令
    print(f"\n📋 部署命令:")
    print(f"kubectl apply -f {args.output}")
    print(f"kubectl get pods -n {config['namespaces'][args.environment]}")


if __name__ == "__main__":
    main()