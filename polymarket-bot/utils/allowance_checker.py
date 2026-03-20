"""
Checks on-chain token allowances for Polymarket contracts.
Uses web3 (included via py-clob-client dependency).
"""
from __future__ import annotations
from typing import Dict

from loguru import logger

from config import settings

# Polymarket contract addresses (Polygon mainnet)
POLYMARKET_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
CTF_CONTRACT = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_CONTRACT = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC.e on Polygon

# Minimum allowance: 1M USDC (in 6-decimal units)
MIN_ALLOWANCE = 10**12


async def check_allowances(address: str) -> Dict[str, bool]:
    """
    Returns dict of {contract_name: has_allowance}.
    Requires web3 to be installed (via py-clob-client).
    """
    result: Dict[str, bool] = {}
    if not settings.has_wallet:
        return result

    try:
        from web3 import Web3

        w3 = Web3(Web3.HTTPProvider("https://polygon-rpc.com"))
        erc20_abi = [
            {
                "constant": True,
                "inputs": [
                    {"name": "owner", "type": "address"},
                    {"name": "spender", "type": "address"},
                ],
                "name": "allowance",
                "outputs": [{"name": "", "type": "uint256"}],
                "type": "function",
            }
        ]
        usdc = w3.eth.contract(
            address=Web3.to_checksum_address(USDC_CONTRACT), abi=erc20_abi
        )
        addr_cs = Web3.to_checksum_address(address)

        exchange_allowance = usdc.functions.allowance(
            addr_cs, Web3.to_checksum_address(POLYMARKET_EXCHANGE)
        ).call()
        ctf_allowance = usdc.functions.allowance(
            addr_cs, Web3.to_checksum_address(CTF_CONTRACT)
        ).call()

        result["exchange"] = exchange_allowance >= MIN_ALLOWANCE
        result["ctf"] = ctf_allowance >= MIN_ALLOWANCE

        if not result["exchange"]:
            logger.warning(
                "ATENÇÃO: Sem allowance para Polymarket Exchange! "
                "Aprove USDC em polygon.polymarket.com"
            )
        if not result["ctf"]:
            logger.warning(
                "ATENÇÃO: Sem allowance para CTF contract! "
                "Aprove USDC em polygon.polymarket.com"
            )

    except ImportError:
        logger.debug("web3 não disponível — skip allowance check")
    except Exception as e:
        logger.warning(f"allowance check erro: {e}")

    return result
