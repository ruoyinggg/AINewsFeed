import socket
import unittest
from unittest.mock import patch

from database import prefer_ipv4


class ConnectionAddressTest(unittest.TestCase):
    def test_ipv4_transport_retains_hostname_and_certificate_verification(self):
        options = {'host': 'example.neon.tech', 'port': '5432',
                   'sslmode': 'verify-full', 'sslrootcert': '/trusted/ca.pem'}
        records = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.10', 5432)),
                   (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.11', 5432)),
                   (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.10', 5432))]
        with patch('database.socket.getaddrinfo', return_value=records) as lookup:
            result = prefer_ipv4(options)
        lookup.assert_called_once_with('example.neon.tech', '5432',
                                       family=socket.AF_INET, type=socket.SOCK_STREAM)
        self.assertEqual(result['hostaddr'], '192.0.2.10,192.0.2.11')
        self.assertEqual(result['host'], 'example.neon.tech,example.neon.tech')
        self.assertEqual(result['sslmode'], 'verify-full')
        self.assertEqual(result['sslrootcert'], '/trusted/ca.pem')
        self.assertNotIn('hostaddr', options)

    def test_dns_failure_does_not_replace_existing_connection_settings(self):
        options = {'host': 'ipv6-only.example', 'sslmode': 'verify-full'}
        with patch('database.socket.getaddrinfo', side_effect=socket.gaierror):
            self.assertEqual(prefer_ipv4(options), options)

    def test_explicit_host_addresses_and_multi_host_failover_are_preserved(self):
        for options in [{'host': 'db.example', 'hostaddr': '192.0.2.20'},
                        {'host': 'primary.example,replica.example'},
                        {'host': '/var/run/postgresql'}]:
            with patch('database.socket.getaddrinfo') as lookup:
                self.assertEqual(prefer_ipv4(options), options)
                lookup.assert_not_called()
